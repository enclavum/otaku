"""The code's structure, held as a test (`docs/code_structure.md`): the
import table between the packages, the one-way orders inside the backend
and the terminal, the mirror — five places with one module list — and
the aliases its twins are imported under, the machinery's privacy, the
doors, the interface's resource classes (their verbs, what they answer,
the call path every route follows), the privacy of the `App`'s members,
the HTTP surface against its spec, every command reached by both
frontends, and the web page's own module graph — JavaScript, read as
text.

A deliberate exception to the unit suite's pure-function rule: the
subject here IS the source tree, so the test reads it — and nothing else
but the interface's classes, imported for their annotations. The tables
mirror the doc; a new package, arrow or twin lands in both in the same
commit.
"""

import ast
import collections.abc
import dataclasses
import enum
import functools
import importlib
import inspect
import re
import types
import typing
from pathlib import Path

import otaku.backend.api
from otaku.backend.api import Changed
from otaku.backend.api.app.settings import SettingsResource
from otaku.backend.api.story import language
from otaku.backend.api.story.messages import PlayEvent
from otaku.terminal.commands import COMMANDS, CommandType
from otaku.terminal.dispatch import HANDLERS
from otaku.web import api as web_api
from otaku.web.server import assets

PACKAGE = "otaku"
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / PACKAGE
_STATIC = _SRC / "web" / "static"

# ---------- the layers ----------

# The doc's import table. formatting is the stdlib-like leaf — anyone
# above may use it — so it is listed per package to keep the table
# explicit. Outside the backend, an arrow into it names the backend
# package it reaches: `backend.api` (the interface) or `backend.launch`
# (the door the app is opened through).
_ALLOWED = {
    # `python -m otaku`: the module entry, which only calls the real one.
    "__main__": {"cli"},
    "cli": {"terminal", "web", "backend.launch", "logging", "update", "formatting"},
    # `web` because `/web` serves this app to a browser and waits: the
    # arrow only points this way, so `web` knows nothing of `terminal`.
    "terminal": {"console", "backend.api", "web", "formatting"},
    "web": {"console", "backend.api", "formatting"},
    # What a frontend draws in the terminal it was LAUNCHED from — a leaf,
    # handed what it draws.
    "console": {"formatting"},
    "agents": {"context", "providers", "store", "logging", "formatting"},
    "backend": {
        "agents",
        "context",
        "providers",
        "integrations",
        "store",
        "settings",
        "encryption",
        "logging",
        "formatting",
    },
    "context": {"store", "providers"},
    # `formatting` for `Money`: a value type, in the leaf everyone may reach.
    "providers": {"settings", "formatting"},
    "integrations": {"settings", "formatting"},
    "store": {"encryption"},
    "settings": {"formatting"},
    "logging": {"providers", "encryption", "formatting"},
    "encryption": {"formatting"},
    "update": set(),
    "formatting": set(),
}

# Inside the backend, one way: `errors` ← `domain.app` ← `domain.codex`
# ← `domain.story` ← `api` ← `samples` ← `launch`, `domain._formats`
# beside `domain.story` needing none of them — the codex reads a card
# file through it too. The launch composes the app, the domain's
# machinery included; the two package roots hold nothing.
_BACKEND = {
    "backend": set(),
    "errors": set(),
    "domain": set(),
    "domain.app": {"errors"},
    "domain._formats": set(),
    "domain.codex": {"errors", "domain.app", "domain._formats"},
    "domain.story": {"errors", "domain.app", "domain.codex", "domain._formats"},
    "api": {"errors", "domain.app", "domain.codex", "domain.story"},
    "samples": {"errors", "domain.app", "domain.story", "api"},
    "launch": {"errors", "domain.app", "domain.story", "domain._formats", "api", "samples"},
}

# Inside the terminal, one way: `api` ← `commands` ← `tty` ← {`prompt`,
# `screens`, `chat`, `help`} ← `dispatch` ← `run` — each package imports
# only those ranked below it, the four of one rank none of each other.
_TERMINAL = {
    "terminal": -1,
    "api": 0,
    "commands": 1,
    "tty": 2,
    "prompt": 3,
    "screens": 3,
    "chat": 3,
    "help": 3,
    "dispatch": 4,
    "run": 5,
}


class TestLayers:
    def test_every_package_imports_only_its_allowed_arrows(self) -> None:
        violations = [
            f"{src} -> {dst}  ({where})"
            for src, dst, where in _edges()
            if dst not in _ALLOWED.get(src, set())
        ]
        assert not violations, "forbidden imports:\n" + "\n".join(violations)

    def test_every_package_has_a_declared_row(self) -> None:
        # A new top-level package takes a row here and in the doc before it
        # may exist — even one that imports nothing yet.
        undeclared = _children(_SRC) - set(_ALLOWED)
        assert not undeclared, f"packages without a declared row: {sorted(undeclared)}"


class TestBackend:
    def test_the_backends_units_import_one_way(self) -> None:
        violations = [
            f"{src} -> {dst}  ({where})"
            for src, dst, where in _inner_edges("backend", _backend_unit)
            if dst not in _BACKEND.get(src, set())
        ]
        assert not violations, "forbidden imports:\n" + "\n".join(violations)

    def test_every_backend_unit_has_a_declared_row(self) -> None:
        units = {_backend_unit(_module_of(path)) for path in _python_files(_SRC / "backend")}
        assert units <= set(_BACKEND), f"undeclared: {sorted(units - set(_BACKEND))}"


class TestTerminal:
    def test_the_terminals_packages_import_one_way(self) -> None:
        violations = [
            f"{src} -> {dst}  ({where})"
            for src, dst, where in _inner_edges("terminal", _terminal_unit)
            if _TERMINAL[dst] >= _TERMINAL[src]
        ]
        assert not violations, "imports against the order:\n" + "\n".join(violations)

    def test_every_terminal_package_has_a_rank(self) -> None:
        units = {_terminal_unit(_module_of(path)) for path in _python_files(_SRC / "terminal")}
        assert units <= set(_TERMINAL), f"unranked: {sorted(units - set(_TERMINAL))}"


# ---------- the mirror ----------

# The four places of modules laid out alike, each as its directory and
# its modules' suffix; the fifth, the page's client, writes the list in its
# own shape — a package one module (`app.js`, `story.js`), a twin a
# section of it headed with the twin's name (`_client_twins`).
_PLACES = {
    "backend.domain": (_SRC / "backend" / "domain", ".py"),
    "backend.api": (_SRC / "backend" / "api", ".py"),
    "web.api": (_SRC / "web" / "api", ".py"),
    "terminal.api": (_SRC / "terminal" / "api", ".py"),
}
_TWINS = ("app", "codex", "story")
_PACKAGE_ROOTS = {"__init__.py", "index.js"}
_CLIENT = _STATIC / "js" / "api"
# A section of the client, headed with its twin's name.
_SECTION = re.compile(r"^// -{10} (\w+) -{10}$", re.M)


class TestMirror:
    def test_the_five_places_hold_one_module_list(self) -> None:
        domain = _twins(*_PLACES["backend.domain"])
        places = {place: _twins(*where) for place, where in _PLACES.items()}
        for place, found in {**places, "web/static/js/api": _client_twins()}.items():
            assert found == domain, (
                f"{place}: missing {sorted(domain - found)}, beyond {sorted(found - domain)}"
            )

    def test_a_place_holds_nothing_beside_its_twins_but_its_machinery(self) -> None:
        for place, (directory, suffix) in _PLACES.items():
            assert _strays(directory, suffix, subdirectories=set(_TWINS)) == [], place
            for twin in _TWINS:
                assert _strays(directory / twin, suffix, subdirectories=set()) == [], (
                    f"{place}/{twin}"
                )
        # the client: a module per package, the facade and its machinery
        assert _strays(_CLIENT, ".js", subdirectories=set()) == []
        packages = {path.stem for path in _CLIENT.glob("*.js")} - {"index"}
        assert {name for name in packages if not name.startswith("_")} == set(_TWINS)

    def test_a_codex_and_a_story_root_hold_nothing(self) -> None:
        # nothing in any `codex` or `story`: their modules are imported by
        # their paths
        for package in ("codex", "story"):
            for place, (directory, _) in _PLACES.items():
                body = ast.parse((directory / package / "__init__.py").read_text()).body
                assert all(_is_docstring(node) for node in body), f"{place}.{package}"
            # the client's package has no section of its own name
            assert package not in _SECTION.findall((_CLIENT / f"{package}.js").read_text())

    def test_dispatch_follows_the_layout_for_the_resources_with_commands(self) -> None:
        dispatch = _SRC / "terminal" / "dispatch"
        assert _twins(dispatch, ".py") <= _twins(*_PLACES["backend.domain"])
        assert _strays(dispatch, ".py", subdirectories=set(_TWINS)) == []

    def test_a_twin_from_the_layer_below_is_imported_under_its_layers_name(self) -> None:
        # an interface module imports a domain module as `domain_<name>`,
        # and the terminal a module of its door as `api_<name>`
        wrong = []
        for path in _python_files(_SRC / "backend" / "api"):
            for found in _imports(path):
                if found.module and found.target.startswith(f"{PACKAGE}.backend.domain."):
                    wrong += _misnamed(path, found, "domain_")
        door = _SRC / "terminal" / "api"
        for path in _python_files(_SRC / "terminal"):
            if path.is_relative_to(door):
                continue
            for found in _imports(path):
                if found.module and found.target.startswith(f"{PACKAGE}.terminal.api."):
                    wrong += _misnamed(path, found, "api_")
        assert not wrong, "\n".join(wrong)


# ---------- the machinery ----------


class TestMachinery:
    def test_machinery_is_imported_by_its_own_layer_alone(self) -> None:
        # the layer above never does, but for the launch, which composes
        # the app out of the domain's parts
        violations = []
        for path in _python_files():
            importer = _module_of(path)
            for found in _imports(path):
                layer = _layer_of(found.target)
                if layer is None or importer == layer or importer.startswith(f"{layer}."):
                    continue
                if importer == f"{PACKAGE}.backend.launch" and layer.startswith(
                    f"{PACKAGE}.backend."
                ):
                    continue
                violations.append(f"{importer} -> {found.target}  (line {found.line})")
        assert not violations, "machinery imported from outside its layer:\n" + "\n".join(
            violations
        )


# ---------- the doors ----------


class TestDoors:
    def test_each_frontend_reaches_the_backend_through_its_door_alone(self) -> None:
        violations = []
        for frontend in ("terminal", "web"):
            door = _SRC / frontend / "api"
            for path in _python_files(_SRC / frontend):
                if path.is_relative_to(door):
                    continue
                for found in _imports(path):
                    if found.target.startswith(f"{PACKAGE}.backend"):
                        violations.append(f"{_module_of(path)} -> {found.target}")
        assert not violations, "\n".join(violations)

    def test_a_door_module_imports_its_own_twin_alone(self) -> None:
        # What it needs of another twin it takes from that twin's sibling
        # in its own door; the root's vocabulary is everyone's.
        violations = []
        for frontend in ("terminal", "web"):
            door = _SRC / frontend / "api"
            for path in _python_files(door):
                if _layer_of(_module_of(path)) is not None:
                    continue  # the door's machinery
                twin = f"{PACKAGE}.backend.api" + _module_of(path).removeprefix(
                    f"{PACKAGE}.{frontend}.api"
                )
                for found in _imports(path):
                    reached = found.target
                    if reached.startswith(f"{PACKAGE}.backend.api") and reached not in {
                        f"{PACKAGE}.backend.api",
                        twin,
                    }:
                        violations.append(f"{_module_of(path)} -> {reached}  (line {found.line})")
        assert not violations, "\n".join(violations)

    def test_the_terminal_door_re_exports_every_type_its_twin_defines(self) -> None:
        # everything its twin answers, so the terminal imports from its door alone
        missing = []
        for module in _interface_modules():
            if module is otaku.backend.api or _layer_of(module.__name__) is not None:
                continue  # the root's vocabulary is the door root's; machinery has no twin
            door = importlib.import_module(
                module.__name__.replace(f"{PACKAGE}.backend.api", f"{PACKAGE}.terminal.api", 1)
            )
            for name, obj in vars(module).items():
                if _is_answered_type(module, name, obj) and getattr(door, name, None) is not obj:
                    missing.append(f"{door.__name__}.{name}")
        assert not missing, "\n".join(missing)

    def test_the_webs_thread_imports_nothing_of_the_app(self) -> None:
        # its runner is generic over the app it is handed
        assert _imports(_SRC / "web" / "thread.py") == []

    def test_the_webs_api_knows_no_thread_and_no_http(self) -> None:
        forbidden = (f"{PACKAGE}.web.server", f"{PACKAGE}.web.thread")
        for path in _python_files(_SRC / "web" / "api"):
            assert not any(found.target.startswith(forbidden) for found in _imports(path)), path
            modules = {
                alias.name.split(".")[0]
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                (node.module or "").split(".")[0]
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, ast.ImportFrom)
            }
            assert not modules & {"http", "socket", "socketserver", "threading"}, path


# ---------- the interface ----------

_FIVE = ("list", "get", "create", "update", "delete")


class TestInterface:
    def test_the_root_re_exports_only_inert_data_but_the_facade_and_the_app(self) -> None:
        # Nothing else with state or behavior can be laundered through it
        # into a frontend.
        alive = [
            name
            for name in otaku.backend.api.__all__
            if name not in {"Api", "App"} and not _is_inert(getattr(otaku.backend.api, name))
        ]
        assert not alive, f"backend.api re-exports with behavior: {alive}"

    def test_every_method_of_a_resource_is_one_of_the_five_verbs(self) -> None:
        others = [
            f"{name}.{method}"
            for name, cls in _resources().items()
            for method, fn in vars(cls).items()
            if inspect.isfunction(fn) and not method.startswith("_") and method not in _FIVE
        ]
        # the one method standing outside the five
        assert others == ["SettingsResource.check_password"]

    def test_a_read_answers_data_and_a_write_what_it_changed(self) -> None:
        wrong = []
        for name, cls in _resources().items():
            for method in _FIVE:
                if not hasattr(cls, method):
                    continue
                answer = typing.get_type_hints(getattr(cls, method))["return"]
                fits = _is_data(answer) if method in {"get", "list"} else _is_written(answer)
                if not fits:
                    wrong.append(f"{name}.{method} -> {answer}")
        assert not wrong, "\n".join(wrong)

    def test_a_resource_holds_the_app_and_its_sub_resources_alone(self) -> None:
        wrong = []
        for path in _python_files(_SRC / "backend" / "api"):
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.ClassDef) and node.name.endswith("Resource"):
                    wrong += [f"{node.name}: {problem}" for problem in _holds_more(node)]
        assert not wrong, "\n".join(wrong)

    def test_a_modules_entities_follow_its_resources(self) -> None:
        late = []
        for path in _python_files(_SRC / "backend" / "api"):
            entity = None
            for node in ast.parse(path.read_text()).body:
                if not isinstance(node, ast.ClassDef):
                    continue
                if not node.name.endswith("Resource"):
                    entity = entity or node.name
                elif entity is not None:
                    late.append(f"{_module_of(path)}: {node.name} after {entity}")
        assert not late, "\n".join(late)

    def test_every_resource_is_reached_from_the_facade(self) -> None:
        reached, waiting = set(), ["Api"]
        while waiting:
            name = waiting.pop()
            reached.add(name)
            waiting += [child for child in _attributes()[name].values() if child not in reached]
        assert set(_resources()) - reached == set()

    def test_every_route_follows_its_call_path(self) -> None:
        # `api.stories.messages.create(5, …)` is POST /api/stories/5/messages:
        # every path walked through the facade's attributes, its method the verb
        wrong = []
        for method, template in _routes() | {("GET", "/api/app/status")}:
            resource, at_id = _resource_at(template)
            if resource is None:
                wrong.append(f"{method} {template}: no attribute of the facade spells it")
                continue
            verb = _verb(method, _resources()[resource], at_id)
            if not hasattr(_resources()[resource], verb):
                wrong.append(f"{method} {template}: {resource} has no {verb}")
        assert not wrong, "\n".join(wrong)


# ---------- the App's members ----------


class TestAppPrivacy:
    def test_the_apps_underscore_surface_stays_in_the_domain(self) -> None:
        # The domain's modules are the implementation and the launch
        # composes the app, the samples' seeding with it; nothing else
        # touches its members — the suites included, which assert through
        # a second Store connection.
        # the handle, never a module path (`domain.app._paths`); split: never matches itself
        needle = re.compile(r"(?<![.\w])(?:app|backend)" + r"\._[a-z]")
        uses = []
        for tree in (PACKAGE, "tests", "scenarios"):
            for path in sorted((_ROOT / tree).rglob("*.py")):
                if "__pycache__" in path.parts or path.is_relative_to(_SRC / "backend" / "domain"):
                    continue
                if path in (
                    _SRC / "backend" / "launch.py",
                    _SRC / "backend" / "samples" / "__init__.py",
                ):
                    continue
                for number, line in enumerate(path.read_text().splitlines(), start=1):
                    if needle.search(line):
                        uses.append(f"{path.relative_to(_ROOT)}:{number}: {line.strip()}")
        assert not uses, "app._ outside the domain:\n" + "\n".join(uses)


# ---------- the HTTP surface ----------

# What the server answers itself, outside the interface's tables: the
# heartbeat, the watch, and signing in.
_SERVER_HELD = {
    ("GET", "/api/app/status"),
    ("GET", "/api/watch"),
    ("GET", "/api/login"),
    ("POST", "/api/login"),
    ("DELETE", "/api/login"),
}


class TestHttpSpec:
    """`otaku/web/api.yaml` is the HTTP surface as OpenAPI, maintained by
    hand. Its paths and their methods are held against the code's own
    tables — the file read as text — so an endpoint added, renamed or
    dropped without the spec fails the suite. The schemas' truth stays
    the review's."""

    def test_the_spec_lists_exactly_what_is_served(self) -> None:
        listed, served = _spec_operations(), _routes() | _SERVER_HELD
        assert listed == served, (
            f"only in the spec: {sorted(listed - served)}; "
            f"only in the code: {sorted(served - listed)}"
        )


# ---------- every command, in both frontends ----------

# How the PAGE reaches every command the terminal answers. HAND-KEPT and
# not derived: the page routes by endpoint, so nothing in the source can
# say which endpoint stands for which command — only a person can. Three
# shapes, and the third is the point of writing it down:
#
#   screen:TOKEN     a row of `SCREENS` in `web/static/js/commands.js`
#   METHOD /path     a route of the HTTP surface, reached by a button
#   none:REASON      deliberately not on the page, and why
#
# A new command fails the suite here until somebody decides which of the
# three it is.
_ON_THE_PAGE = {
    "/undo": "screen:/undo",
    "/regen": "screen:/regen",
    "/last": "screen:/last",
    "/clear": "screen:/clear",
    "/stories": "screen:/stories",
    "/fork": "screen:/fork",
    "/system": "screen:/system",
    # the story browser's Rename
    "/title": "PATCH /api/stories/{story}",
    "/new": "screen:/new",
    "/lore": "screen:/lore",
    "/cast": "screen:/cast",
    "/extract": "screen:/extract",
    # the cast pane's Merge into… writes the same fold
    "/merge": "POST /api/stories/{story}/characters/{character}/merges",
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
    # the layout switch in the contents rail writes the setting the
    # terminal's two modes store
    "/set mode": "PATCH /api/stories/{story}/settings/{setting}",
    # the Support request button in the Help docket's footer opens it
    "/support": "screen:/support",
    "/help": "screen:/help",
    # The one gap that can never be filled: this command's whole purpose
    # is to reach the page, and the reader of the page is already there.
    "/web": "none:the page IS this — there is nowhere for it to send anyone",
    "/bye": "screen:/bye",
}


class TestFrontendParity:
    """A command is declared once, in the terminal's table, and reachable
    in both frontends. The terminal dispatches by token, so its half is
    derived (`dispatch.HANDLERS`); the page routes by endpoint, so its
    half is the hand-kept `_ON_THE_PAGE` above, which is also where a
    deliberate gap is recorded instead of going quiet."""

    def test_every_command_is_answered_by_the_terminal(self) -> None:
        assert _dispatchable() - set(HANDLERS) == set()

    def test_every_command_says_how_the_page_reaches_it(self) -> None:
        assert _dispatchable() == set(_ON_THE_PAGE)

    def test_every_screen_it_names_exists(self) -> None:
        assert _screens_named() - _web_screens() == set()

    def test_every_endpoint_it_names_is_served(self) -> None:
        served = {f"{method} {template}" for method, template in _routes()}
        assert _endpoints_named() - served == set()

    def test_every_row_says_which_of_the_three_it_is(self) -> None:
        # A misspelt `screen:` would otherwise read as an endpoint and fail
        # somewhere else, saying something that is not true.
        for token, row in _ON_THE_PAGE.items():
            shaped = row.startswith(("screen:", "none:")) or re.fullmatch(r"[A-Z]+ /api/\S+", row)
            assert shaped, f"{token} says {row!r}, which is none of the three shapes"

    def test_neither_frontend_answers_a_command_that_does_not_exist(self) -> None:
        assert set(HANDLERS) - _answerable() == set()
        assert _web_screens() - _answerable() == set()


# ---------- the page ----------


class TestPageModules:
    """The page is a set of ES modules with no build step and nothing to
    enforce their direction but a habit. The graph below is that habit
    written down: a module may import only what its row allows, its
    groups one way (`_GROUPS`), and the edges that matter are one-way —
    `commands` reaches the screens, never the other way, or a screen could
    not be opened from the table that routes to it; `language` is a leaf,
    so the composer's menu and the transcript's highlighting depend on no
    screen; and the client (`api/`) is the one way to the server, entered
    by its root."""

    def test_every_module_imports_only_what_its_row_allows(self) -> None:
        for module, imported in _page_imports().items():
            assert imported <= _PAGE[module], f"{module} imports {imported - _PAGE[module]}"

    def test_every_module_is_in_the_table(self) -> None:
        assert set(_page_imports()) | {"api"} == set(_PAGE)

    def test_the_groups_import_one_way(self) -> None:
        # api ← kit ← play ← screens ← {commands, composer} ← app
        for path in _page_files():
            source = _GROUPS.index(_group(path.relative_to(_STATIC).as_posix()))
            for spec in _page_specs(path):
                target = _GROUPS.index(_group(spec))
                assert target <= source, (path.name, spec)

    def test_every_module_is_served(self) -> None:
        # A module the server does not list is a 404 at the first import.
        written = {
            path.relative_to(_STATIC).as_posix()
            for path in [_STATIC / "app.js", *(_STATIC / "js").rglob("*.js")]
        }
        assert written <= set(assets.SCRIPTS)

    def test_a_screen_enters_the_client_by_its_root(self) -> None:
        for path in _page_files():
            for spec in _page_specs(path):
                assert not spec.startswith("js/api/") or spec == "js/api/index", (path.name, spec)

    def test_the_clients_modules_import_nothing_beside_it(self) -> None:
        client = _STATIC / "js" / "api"
        for path in client.rglob("*.js"):
            for spec in re.findall(r'from "([^"]+)"', path.read_text()):
                assert (path.parent / spec).resolve().is_relative_to(client), (path.name, spec)

    def test_the_screens_reach_the_server_through_the_client_alone(self) -> None:
        # the watch excepted: the server's own channel, as the sign-in is
        for path in _page_files():
            source = path.read_text()
            if path.name == "shell.js":
                source = source.replace('"/api/watch"', "")
            assert "fetch(" not in source, path.name
            assert not re.search(r"[\"'`]/api/", source), path.name


# What each page module may import, by its group (`_GROUPS`). `api` (the
# client) and the kit's `dom` and `format` are the leaves; `held`, the
# story the page holds, sits just above the client; `browser` is what a
# screen is built from; one module per screen; `commands` is the dispatch
# over all of them; `app` is the composition root and may reach anything.
_PAGE = {
    "api": set(),
    # ---- kit: what every part is built from
    "dom": set(),
    "format": set(),
    # The one line otaku speaks in. A leaf, so everything with something
    # to say can reach it without reaching for a screen.
    "status": {"dom"},
    "prose": {"dom"},
    "browser": {"dom", "status"},
    # A turn's pictures: the tiles and the lightbox, drawn by the
    # transcript and by the dossier's reader alike, so below both.
    "pictures": {"api", "dom"},
    # ---- play: the story as the page plays it
    "language": set(),
    # The story this page plays: read by every screen that acts on one,
    # drawn by none.
    "held": {"api"},
    # `browser` for the kit's editor: a stored turn is corrected where it
    # is read, as the dossier corrects one.
    "transcript": {"api", "browser", "dom", "held", "language", "pictures", "prose", "status"},
    # The frame around the transcript — the layout switch, the facts, the
    # watch — `format` for the runhead's story name, `browser` for `guard`.
    "shell": {"api", "browser", "dom", "format", "held", "status", "transcript"},
    # ---- screens: what opens over the page
    # Help and the support docket its footer opens.
    "help": {"api", "browser", "dom", "language"},
    # The dossier under the browser: the browser reaches into it (Open
    # story), never the other way — its route back is a command.
    # `characters` for the Cast tab's card: linked, picked, invited.
    "dossier": {
        "api",
        "browser",
        "characters",
        "dom",
        "format",
        "held",
        "pictures",
        "prose",
        "shell",
        "tools",
    },
    # All characters, and the card picker every "which card" goes through;
    # `transfer` for the card dialog an invite and an import open.
    "characters": {"api", "browser", "dom", "format", "held", "pictures", "shell", "transfer"},
    # The dossier's fifth tab in a module of its own. `models` for the
    # mask a set key reads behind, as a provider's does.
    "tools": {"api", "browser", "dom", "models", "shell"},
    "stories": {"api", "browser", "dom", "dossier", "format", "held", "shell", "transfer"},
    # `format` for a model's size and window, written as the catalogs write them.
    "models": {"api", "browser", "dom", "format", "shell"},
    # `shell` to draw the story's facts and context use again after a
    # setting that changes them.
    "settings": {"api", "browser", "dom", "shell"},
    "reports": {"api", "browser", "dom", "format", "held"},
    "transfer": {"api", "browser", "dom", "held", "shell", "status"},
    # The sign-in dialog and the rail's way out: `app` hands it to
    # `whenUnauthorized`, so a refused request can wait on it without the
    # client importing anything.
    "login": {"api", "dom"},
    # ---- the doors above the screens
    "commands": {
        "api",
        "browser",
        "characters",
        "dom",
        "dossier",
        "held",
        "help",
        "models",
        "reports",
        "settings",
        "shell",
        "status",
        "stories",
        "transcript",
        "transfer",
    },
    "composer": {"api", "commands", "dom", "held", "language", "status", "transcript"},
    "app": {
        "api",
        "browser",
        "commands",
        "composer",
        "dom",
        "held",
        "language",
        "login",
        "shell",
        "transcript",
    },
}

# The page's groups, from the bottom: a module imports only from its own
# group or one below it.
_GROUPS = ("js/api", "js/kit", "js/play", "js/screens", "js", "app")


class TestPageCaptions:
    """The page holds hand-kept captions for what the backend declares —
    the composer menu's `_MENU`, the help sheet's `_MEANS`, the settings
    slip's `_ABOUT` — because a caption is the medium's own words. Like
    `_ON_THE_PAGE`, each is a decision only a person can record, so a new
    word of the language or a new setting fails here until its caption
    exists, instead of rendering blank."""

    def test_every_word_of_the_language_has_a_menu_caption(self) -> None:
        bare = {row.token.removeprefix("… ") for row in language.OPENERS + language.INLINERS}
        assert bare - _js_keys("composer.js", "_MENU") == set()

    def test_every_word_of_the_language_has_a_help_meaning(self) -> None:
        words = {row.token for row in language.OPENERS + language.INLINERS}
        assert words - _js_keys("help.js", "_MEANS") == set()

    def test_every_setting_a_reader_sets_is_drawn_and_captioned_on_the_slip(self) -> None:
        # by the name the slip gives it: the context limit is `max_context` there
        source = _page_source("settings.js")
        shown = {_SLIP_NAMES.get(name, name) for name in _settable()}
        for name in shown:
            assert f'"{name}"' in source, f"the settings slip never draws {name}"
        assert shown - _js_keys("settings.js", "_ABOUT") == set()


# The installation's settings the slip names otherwise than the interface.
_SLIP_NAMES = {"context_limit": "max_context"}


class TestWebTheme:
    """`docs/web_tokens.md` is the web's public theming contract: every
    token `:root` declares is documented and nothing undeclared is — and
    the dark theme, one list written twice (once to pin, once to follow
    the OS), stays ONE list. Nothing else fails when either drifts, so it
    is held here."""

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


class TestDemo:
    """The deployable web demo (`demo/web/`) fakes the whole HTTP surface
    in the visitor's browser. Its router must know every path the spec
    lists — read as text, like everything else here — or a new endpoint
    ships with a demo that silently cannot answer it."""

    def test_the_demo_routes_every_path_the_spec_lists(self) -> None:
        router = (_ROOT / "demo" / "web" / "demo.js").read_text()
        missing = sorted({path for _, path in _spec_operations() if path not in router})
        assert not missing, f"paths the demo does not route: {missing}"


# ---------- reading the tree ----------


@dataclasses.dataclass(frozen=True)
class _Import:
    """One import of the tree in a file: the module it reaches (`from a
    import b` reaches `a.b` where `b` is a module, else `a`), whether a
    module is what it binds, the name it binds it under, and its line."""

    target: str
    module: bool
    alias: str | None
    line: int


def _python_files(under: Path = _SRC) -> list[Path]:
    return sorted(path for path in under.rglob("*.py") if "__pycache__" not in path.parts)


def _module_of(path: Path) -> str:
    """The dotted module a file is — a package's `__init__` the package."""
    parts = path.relative_to(_ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


@functools.cache
def _listing(directory: Path) -> frozenset[str]:
    """The names in a directory as they are spelt: a case-insensitive file
    system answers `exists()` for a name spelt otherwise."""
    return (
        frozenset(path.name for path in directory.iterdir()) if directory.is_dir() else frozenset()
    )


def _is_module(dotted: str) -> bool:
    """Whether a dotted name is a module or a package of the tree."""
    *parents, last = dotted.split(".")
    for depth, part in enumerate(parents):
        if part not in _listing(_ROOT.joinpath(*parents[:depth])):
            return False
    names = _listing(_ROOT.joinpath(*parents))
    package = last in names and "__init__.py" in _listing(_ROOT.joinpath(*parents, last))
    return f"{last}.py" in names or package


@functools.cache
def _imports(path: Path) -> list[_Import]:
    """Every import of the tree in one file, a relative one resolved
    against the importing file's package, so none can dodge the checks."""
    package = (
        _module_of(path) if path.name == "__init__.py" else _module_of(path).rpartition(".")[0]
    )
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [
                _Import(alias.name, True, alias.asname, node.lineno)
                for alias in node.names
                if alias.name.split(".")[0] == PACKAGE
            ]
        elif isinstance(node, ast.ImportFrom):
            source = node.module or ""
            if node.level:
                base = package.split(".")
                source = ".".join([*base[: len(base) - (node.level - 1)], *filter(None, [source])])
            if source.split(".")[0] != PACKAGE:
                continue
            for alias in node.names:
                inner = f"{source}.{alias.name}"
                if _is_module(inner):
                    found.append(_Import(inner, True, alias.asname or alias.name, node.lineno))
                else:
                    found.append(_Import(source, False, None, node.lineno))
    return found


def _children(directory: Path) -> set[str]:
    """The packages and modules directly under a directory."""
    out = set()
    for name in _listing(directory):
        if name.endswith(".py") and name != "__init__.py":
            out.add(name.removesuffix(".py"))
        elif "__init__.py" in _listing(directory / name):
            out.add(name)
    return out


def _edges() -> list[tuple[str, str, str]]:
    """Every import across the top-level packages as (source, target,
    file:line) — into the backend from outside it, the target naming the
    backend package it reaches (`backend.api`)."""
    out = []
    for path in _python_files():
        src = _top(_module_of(path))
        for found in _imports(path):
            dst = _top(found.target)
            if dst is None or dst == src:
                continue
            if dst == "backend":
                parts = found.target.split(".")
                dst = ".".join(parts[1:3]) if len(parts) > 2 else "backend"
            out.append((src, dst, f"{path.relative_to(_ROOT)}:{found.line}"))
    return out


def _top(module: str) -> str | None:
    """The top-level package a module belongs to; None for the root."""
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else None


def _inner_edges(package: str, unit: typing.Callable[[str], str]) -> list[tuple[str, str, str]]:
    """Every import between the units of one top-level package, as
    (source unit, target unit, file:line)."""
    out = []
    for path in _python_files(_SRC / package):
        src = unit(_module_of(path))
        for found in _imports(path):
            if _top(found.target) != package:
                continue
            dst = unit(found.target)
            if dst != src:
                out.append((src, dst, f"{path.relative_to(_ROOT)}:{found.line}"))
    return out


def _backend_unit(module: str) -> str:
    """The backend unit a module belongs to: `api`, `launch`, `errors`,
    each package of the domain (`domain.story`), or a package root."""
    parts = module.split(".")[2:]
    if not parts:
        return "backend"
    if parts[0] == "domain" and len(parts) > 1:
        return f"domain.{parts[1]}"
    return parts[0]


def _terminal_unit(module: str) -> str:
    parts = module.split(".")
    return parts[2] if len(parts) > 2 else "terminal"


def _layer_of(module: str) -> str | None:
    """The layer whose machinery a module is — the path before its first
    protected part, at most three deep (`otaku.backend.domain`,
    `otaku.web.api`, `otaku.encryption`); None for a module of a
    surface."""
    parts = module.split(".")
    for at, part in enumerate(parts):
        if part.startswith("_") and not part.startswith("__"):
            return ".".join(parts[: min(at, 3)])
    return None


def _client_twins() -> set[tuple[str, str]]:
    """The page's client's twins as (package, module), read off its
    sections — the `app` section the app root's resources, left out as a
    package root is."""
    return {
        (twin, name)
        for twin in _TWINS
        for name in _SECTION.findall((_CLIENT / f"{twin}.js").read_text())
        if name != twin
    }


def _twins(directory: Path, suffix: str) -> set[tuple[str, str]]:
    """A place's twins as (package, module), its machinery and its
    package roots left out."""
    return {
        (twin, name.removesuffix(suffix))
        for twin in _TWINS
        for name in _listing(directory / twin)
        if name.endswith(suffix) and name not in _PACKAGE_ROOTS and not name.startswith("_")
    }


def _strays(directory: Path, suffix: str, *, subdirectories: set[str]) -> list[str]:
    """What a directory holds besides its modules, its package root, the
    subdirectories it is allowed and its machinery."""
    return sorted(
        name
        for name in _listing(directory)
        if name not in subdirectories | _PACKAGE_ROOTS | {"__pycache__"}
        and not name.startswith("_")
        and not (name.endswith(suffix) and (directory / name).is_file())
    )


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


def _misnamed(path: Path, found: _Import, prefix: str) -> list[str]:
    """The import of a twin module under a name other than its layer's."""
    want = prefix + found.target.rpartition(".")[2]
    if found.alias == want:
        return []
    return [f"{_module_of(path)}:{found.line}: {found.target} as {found.alias}, not {want}"]


@functools.cache
def _interface_modules() -> tuple[types.ModuleType, ...]:
    """The interface's modules, its root first."""
    return tuple(
        importlib.import_module(_module_of(path))
        for path in _python_files(_SRC / "backend" / "api")
    )


@functools.cache
def _resources() -> dict[str, type]:
    """Every resource class of the interface, by name."""
    out: dict[str, type] = {}
    for module in _interface_modules():
        for name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and name.endswith("Resource")
                and obj.__module__ == module.__name__
            ):
                assert name not in out, f"two resources named {name}"
                out[name] = obj
    return out


@functools.cache
def _attributes() -> dict[str, dict[str, str]]:
    """Each class of the interface, by name, and the resource each of its
    attributes holds — `self.messages = MessagesResource(app)` — read off
    its `__init__`."""
    out: dict[str, dict[str, str]] = {}
    for path in _python_files(_SRC / "backend" / "api"):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.ClassDef):
                continue
            held = out.setdefault(node.name, {})
            for statement in _init_of(node):
                if (
                    isinstance(statement, ast.Assign)
                    and isinstance(statement.value, ast.Call)
                    and isinstance(statement.value.func, ast.Name)
                ):
                    for target in statement.targets:
                        if isinstance(target, ast.Attribute) and not target.attr.startswith("_"):
                            held[target.attr] = statement.value.func.id
    return out


def _init_of(node: ast.ClassDef) -> list[ast.stmt]:
    init = next(
        (f for f in node.body if isinstance(f, ast.FunctionDef) and f.name == "__init__"), None
    )
    return init.body if init is not None else []


def _holds_more(node: ast.ClassDef) -> list[str]:
    """What a resource class holds beyond the app and its sub-resources."""
    init = next(
        (f for f in node.body if isinstance(f, ast.FunctionDef) and f.name == "__init__"), None
    )
    if init is None:
        return ["no __init__"]
    problems = []
    if [arg.arg for arg in init.args.args] != ["self", "app"] or init.args.kwonlyargs:
        problems.append("is built over something besides the app")
    for statement in init.body:
        if _is_docstring(statement):
            continue
        holds_app = (
            isinstance(statement, ast.Assign)
            and ast.unparse(statement.targets[0]) == "self._app"
            and ast.unparse(statement.value) == "app"
        )
        holds_child = (
            isinstance(statement, ast.Assign)
            and isinstance(statement.value, ast.Call)
            and ast.unparse(statement.value.func).endswith("Resource")
            and ast.unparse(statement.value).endswith("(app)")
        )
        if not (holds_app or holds_child):
            problems.append(f"holds {ast.unparse(statement)}")
    return problems


def _resource_at(template: str) -> tuple[str | None, bool]:
    """The resource class a path names, walked through the facade's
    attributes (a kebab segment its snake attribute), and whether the path
    ends at an id."""
    resource, at_id = "Api", False
    for segment in template.removeprefix("/api/").split("/"):
        if segment.startswith("{"):
            at_id = True
            continue
        resource = _attributes().get(resource, {}).get(segment.replace("-", "_"))
        if resource is None:
            return None, False
        at_id = False
    return resource, at_id


def _verb(method: str, resource: type, at_id: bool) -> str:
    """The verb a method asks of a resource: a GET of one — or of a
    resource that is no collection — `get`, of a collection `list`."""
    if method != "GET":
        return {"POST": "create", "PATCH": "update", "DELETE": "delete"}[method]
    return "get" if at_id or not hasattr(resource, "list") else "list"


def _is_data(answer: object) -> bool:
    """An entity, a list of them, a sentence: data a client shows."""
    if answer is str:
        return True
    if typing.get_origin(answer) in (list, tuple):
        return all(arg is Ellipsis or _is_data(arg) for arg in typing.get_args(answer))
    return (
        isinstance(answer, type)
        and dataclasses.is_dataclass(answer)
        and answer.__dataclass_params__.frozen  # type: ignore[attr-defined]
    )


def _is_written(answer: object) -> bool:
    """A write's answer: what it changed (`Changed[T]`, `Changed[None]`
    where nothing stands), the reply's events as they stream, or — a
    write asked to validate only — its check beside the change."""
    if typing.get_origin(answer) is Changed:
        return all(arg is type(None) or _is_data(arg) for arg in typing.get_args(answer))
    if typing.get_origin(answer) is collections.abc.Iterator:
        (events,) = typing.get_args(answer)
        return set(typing.get_args(events)) == set(typing.get_args(PlayEvent))
    if typing.get_origin(answer) in (typing.Union, types.UnionType):
        members = typing.get_args(answer)
        return any(_is_written(m) for m in members) and all(
            _is_written(m) or _is_data(m) for m in members
        )
    return False


def _is_inert(obj: object) -> bool:
    """Data, not behavior: a frozen dataclass, an enum, an exception type,
    a sentence — a type alias, a name for a shape with nothing behind it —
    or a constant: a member of an enum, an instance of a frozen dataclass."""
    if isinstance(obj, str | enum.Enum):
        return True
    if isinstance(obj, types.UnionType) or typing.get_origin(obj) is not None:
        return True
    if not isinstance(obj, type):
        return dataclasses.is_dataclass(obj) and type(obj).__dataclass_params__.frozen  # type: ignore[attr-defined]
    if issubclass(obj, BaseException | enum.Enum):
        return True
    return dataclasses.is_dataclass(obj) and obj.__dataclass_params__.frozen  # type: ignore[attr-defined]


def _is_answered_type(module: types.ModuleType, name: str, obj: object) -> bool:
    """A type an interface module defines for what it answers: a frozen
    dataclass or an enum of its own, never a resource."""
    return (
        inspect.isclass(obj)
        and obj.__module__ == module.__name__
        and not name.startswith("_")
        and not name.endswith("Resource")
        and (dataclasses.is_dataclass(obj) or issubclass(obj, enum.Enum))
    )


def _routes() -> set[tuple[str, str]]:
    """Every method and path the interface's tables answer."""
    return set(web_api.ROUTES) | set(web_api.STREAMS)


def _spec_text() -> str:
    return (_SRC / "web" / "api.yaml").read_text()


def _spec_operations() -> set[tuple[str, str]]:
    """Every method and path the hand-kept OpenAPI lists — read as text,
    like everything else this module reads."""
    found, path = set(), None
    for line in _spec_text().splitlines():
        if heading := re.fullmatch(r"  (/api/\S+):", line):
            path = heading.group(1)
        elif line and not line.startswith(" "):
            path = None
        elif path and (operation := re.fullmatch(r"    (get|post|put|patch|delete):", line)):
            found.add((operation.group(1).upper(), path))
    return found


def _settable() -> list[str]:
    """The installation's settings a reader sets — every field an update
    of them takes but the story to resume, which a landing writes."""
    parameters = inspect.signature(SettingsResource.update).parameters
    return [
        name for name, p in parameters.items() if p.kind is p.KEYWORD_ONLY and name != "last_story"
    ]


def _dispatchable() -> set[str]:
    """Every command a frontend must answer. SYNTAX rows are the story's
    own language — they play, they do not dispatch."""
    return {spec.token for spec in COMMANDS if spec.kind is not CommandType.SYNTAX}


def _answerable() -> set[str]:
    """What a frontend is allowed to answer: a declared row, or the first
    word of a FAMILY of them. `/set` is not a command — the table declares
    `/set think`, `/set verbose` and the rest — but a frontend may open
    one screen for the family, as the page does."""
    declared = {spec.token for spec in COMMANDS}
    return declared | {token.split(" ")[0] for token in declared if " " in token}


def _web_screens() -> set[str]:
    """Every screen the page opens by token — a JavaScript object literal,
    read as text."""
    source = _page_source("commands.js")
    body = source.split("const SCREENS = {", 1)[1].split("\n};", 1)[0]
    return set(re.findall(r'^\s*"(/[a-z ]+)":', body, re.M))


def _screens_named() -> set[str]:
    """The screens `_ON_THE_PAGE` says a command opens."""
    return {
        row.removeprefix("screen:") for row in _ON_THE_PAGE.values() if row.startswith("screen:")
    }


def _endpoints_named() -> set[str]:
    """The routes it says a button calls — anything that is neither a
    screen nor a declared gap."""
    return {row for row in _ON_THE_PAGE.values() if not row.startswith(("screen:", "none:"))}


def _page_files() -> list[Path]:
    """The page's own modules: the root and every module of its groups,
    the client left out."""
    client = _STATIC / "js" / "api"
    return [
        _STATIC / "app.js",
        *sorted(path for path in (_STATIC / "js").rglob("*.js") if not path.is_relative_to(client)),
    ]


def _page_specs(path: Path) -> list[str]:
    """What one page module imports, each resolved to its place under the
    page's root, the suffix dropped — `../kit/dom.js` read as `js/kit/dom`."""
    return [
        (path.parent / spec).resolve().relative_to(_STATIC.resolve()).as_posix()
        for spec in re.findall(r'from "(\.[^"]+)\.js"', path.read_text())
    ]


def _page_imports() -> dict[str, set[str]]:
    """Every page module and the modules it imports, by their stems —
    the client by its root, `api`."""
    return {
        path.stem: {
            "api" if spec.startswith("js/api/") else spec.rsplit("/", 1)[-1]
            for spec in _page_specs(path)
        }
        for path in _page_files()
    }


def _group(module: str) -> str:
    """The group a module stands in, by its place under the page's root
    (`js/kit/dom` → `js/kit`; `js/commands` → `js`; `app.js` → `app`)."""
    if module.removesuffix(".js") == "app":
        return "app"
    folder = module.rsplit("/", 1)[0]
    return folder if folder in _GROUPS else "js"


def _page_source(name: str) -> str:
    """A page module's source, found in whichever group holds it."""
    return next(path for path in (_STATIC / "js").rglob(name)).read_text()


def _js_keys(name: str, constant: str) -> set[str]:
    """The keys of one JavaScript object literal, read as text — quoted
    (`"/me":`) or bare (`verbose:`)."""
    body = _page_source(name).split(f"const {constant} = {{", 1)[1].split("\n};", 1)[0]
    return {quoted or bare for quoted, bare in re.findall(r'^\s*(?:"([^"]+)"|(\w+)):', body, re.M)}


def _app_css() -> str:
    return (_STATIC / "app.css").read_text()


def _light_block() -> str:
    return _app_css().split(":root {", 1)[1].split("\n}", 1)[0]


def _dark_overrides(opener: str) -> dict[str, str]:
    """One dark block's `--otk-*` declarations, name to value."""
    block = _app_css().split(opener, 1)[1].split("}", 1)[0]
    return {
        name: value.strip() for name, value in re.findall(r"(--otk-[a-z-]+):\s*([^;]+);", block)
    }
