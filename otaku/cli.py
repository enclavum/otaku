"""otaku command-line entry point.

otaku                  pick a model and start chatting (interactive picker)
otaku <model>          start an interactive chat (bare name or PROVIDER/MODEL)
otaku <model> <prompt> one-shot: send <prompt> (and/or piped stdin), print, exit
otaku stop <model>     unload a model; `otaku stop --all` unloads everything

There is no `run` subcommand: a first positional that isn't a known command
is routed to the hidden `chat` command by `CompactHelpGroup.resolve_command`.
"""

from __future__ import annotations

import sys

import click
import typer
from typer.core import TyperCommand, TyperGroup

from otaku import __version__, config
from otaku.chat import repl
from otaku.chat.commands import apply_settings
from otaku.chat.inference import State, run_oneshot
from otaku.chat.summary import SummaryWorker
from otaku.client import client_for, map_providers, probing_notice, unreachable_help
from otaku.config import CONFIG_PATH, Config, Provider
from otaku.storage import crypto
from otaku.storage.store import Store
from otaku.text import format_context, format_size, pretty_path

DESCRIPTION = "Multi-provider chat client for OpenAI-compatible servers."

# Hidden command that a bare `otaku <model> [prompt]` invocation is routed to
# (see CompactHelpGroup.resolve_command). There is no visible `run` subcommand.
_DEFAULT_CMD = "chat"


def _format_opt_decl(opt: click.Option) -> str:
    """Render an option as `-h, --help` (shorts before longs)."""
    shorts = [o for o in opt.opts if o.startswith("-") and not o.startswith("--")]
    longs = [o for o in opt.opts if o.startswith("--")]
    return ", ".join(shorts + longs)


def _opt_help(opt: click.Option, command_name: str) -> str:
    """Synthesize a cobra-style `help for <name>` for the auto-injected
    --help option; otherwise return the option's own help text."""
    if "--help" in opt.opts:
        return f"help for {command_name}"
    return opt.help or ""


# typer 0.26 types its click-derived methods as `typer._click.*`, which strict
# mypy treats as distinct from the standard `click.*` used in the annotations
# here; they're the same classes at runtime, so the `# type: ignore`s below are
# purely nominal (and will flag as unused if typer ever realigns the names).
class CompactHelpGroup(TyperGroup):
    """Render top-level `otaku --help` (and the no-args invocation) in a
    compact format: short header, `Usage:` block, `Available Commands:`,
    `Flags:`, footer pointing at per-command help.
    """

    def resolve_command(  # type: ignore[override]
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        """Route a first positional that isn't a known subcommand to the hidden
        `chat` command, so `otaku <model>` works without a `run` subcommand
        while `otaku list` / `otaku stop` still dispatch normally."""
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            args = [_DEFAULT_CMD, *args]
        return super().resolve_command(ctx, args)  # type: ignore[arg-type, return-value]

    def get_help(self, ctx: click.Context) -> str:  # type: ignore[override]
        prog = ctx.info_name or "otaku"
        cmds = sorted(self.list_commands(ctx))  # type: ignore[arg-type]
        col = max(12, max((len(n) for n in cmds), default=0) + 4)
        lines: list[str] = [
            DESCRIPTION,
            "",
            "Usage:",
            f"  {prog} [MODEL] [PROMPT]   chat; PROMPT (or piped stdin) runs one-shot",
            f"  {prog} [command]",
            f"  {prog} [flags]",
            "",
            "Available Commands:",
        ]
        for name in cmds:
            cmd = self.get_command(ctx, name)  # type: ignore[arg-type]
            if cmd is None or getattr(cmd, "hidden", False):
                continue
            short = cmd.get_short_help_str(limit=80)
            lines.append(f"  {name:<{col}}{short}")
        lines += [
            "",
            "Flags:",
            f"  -h, --help        help for {prog}",
            "  -nr, --no-record  Don't save conversation to the database",
            "  -v, --version     Show version information",
            "",
            f"Config: {pretty_path(CONFIG_PATH)}",
            f'Use "{prog} [command] --help" for more information about a command.',
        ]
        return "\n".join(lines)


class CompactHelpCommand(TyperCommand):
    """Render `otaku <subcommand> --help` in the same compact cobra-style
    format as `CompactHelpGroup` (description, `Usage:`, `Arguments:`,
    `Flags:`) instead of typer's default boxed Rich layout.
    """

    def get_help(self, ctx: click.Context) -> str:  # type: ignore[override]
        prog = ctx.command_path
        name = ctx.info_name or "command"
        params = self.get_params(ctx)  # type: ignore[arg-type]
        # typer's TyperArgument/TyperOption don't subclass click.Argument/Option
        # under some typer+click combos, so `isinstance` silently drops every
        # param (no Arguments/Flags rendered). `param_type_name` is stable across
        # versions: "argument" / "option" / "parameter".
        args = [p for p in params if p.param_type_name == "argument"]
        opts = [p for p in params if p.param_type_name == "option"]

        usage_parts = [prog]
        if opts:
            usage_parts.append("[flags]")
        for arg in args:
            metavar = (arg.metavar or (arg.name or "")).upper()
            usage_parts.append(metavar if arg.required else f"[{metavar}]")

        lines: list[str] = []
        desc = (self.help or self.short_help or "").strip()
        if desc:
            lines.extend(desc.splitlines())
            lines.append("")
        lines.append("Usage:")
        lines.append(f"  {' '.join(usage_parts)}")

        arg_rows = [
            ((arg.metavar or (arg.name or "")).upper(), getattr(arg, "help", "") or "")
            for arg in args
        ]
        if any(h for _, h in arg_rows):
            lines.append("")
            lines.append("Arguments:")
            col = max(12, max(len(r[0]) for r in arg_rows) + 4)
            for label, h in arg_rows:
                lines.append(f"  {label:<{col}}{h}".rstrip())

        if opts:
            lines.append("")
            lines.append("Flags:")
            opt_rows = [(_format_opt_decl(o), _opt_help(o, name)) for o in opts]  # type: ignore[arg-type]
            col = max(12, max(len(r[0]) for r in opt_rows) + 4)
            for label, h in opt_rows:
                lines.append(f"  {label:<{col}}{h}".rstrip())

        return "\n".join(lines)


app: typer.Typer = typer.Typer(
    cls=CompactHelpGroup,
    no_args_is_help=False,
    add_completion=False,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"otaku {__version__}")
        raise typer.Exit()


def _pick_model_spec(cfg: Config) -> str | None:
    """Open the model picker and return the chosen `provider/model` spec
    (or None on cancel). Saves the choice to ~/.otaku/last_model on confirm.
    """
    from otaku.pickers import model as model_picker  # local: prompt_toolkit slow

    spec = model_picker.pick_model(cfg.providers, initial_spec=config.read_last_model())
    if spec is None:
        return None
    config.write_last_model(spec)
    return spec


def _resolve_spec(cfg: Config, spec: str, *, loaded_only: bool = False) -> tuple[str, str]:
    """Resolve a CLI spec to `(provider_name, model)`.

    Accepts both forms:
      - `provider/model`  (head must be a configured provider; rest is the
        model and may itself contain slashes, e.g. `ollama/hf.co/foo`).
      - `model`           (bare name — every configured provider is queried
        via `list_models()` (or `loaded_models()` when `loaded_only=True`)
        and the unique match wins).

    Raises `ValueError` when the bare name isn't found in any provider, or
    when it's found in more than one and the call site needs an explicit
    `provider/model` to disambiguate. Providers that error during the
    probe are silently skipped (best-effort).
    """
    if "/" in spec:
        head, _, rest = spec.partition("/")
        if head in cfg.providers and rest:
            return head, rest

    def probe(prov_name: str, provider: Provider) -> str | None:
        cli = client_for(provider)
        try:
            models = cli.loaded_models() if loaded_only else cli.list_models(timeout=3.0)
        except Exception:
            return None  # provider down/errored — best-effort, skip it
        return prov_name if spec in set(models) else None

    with probing_notice(cfg.providers):
        matches = [name for name in map_providers(cfg.providers, probe) if name]

    if len(matches) == 1:
        return matches[0], spec

    known = ", ".join(sorted(cfg.providers))
    if not matches:
        scope = "loaded" if loaded_only else "available"
        raise ValueError(f"model {spec!r} not {scope} in any configured provider ({known})")
    raise ValueError(
        f"model {spec!r} is in multiple providers ({', '.join(matches)}); "
        f"disambiguate as '<provider>/{spec}'"
    )


def _read_stdin() -> str:
    """Return piped stdin, or '' when stdin is an interactive terminal (so an
    interactive `otaku <model>` never blocks waiting on stdin)."""
    if sys.stdin.isatty():
        return ""
    try:
        return sys.stdin.read()
    except (OSError, ValueError):
        return ""


def _compose_oneshot(prompt: str, stdin_text: str) -> str | None:
    """Combine a positional prompt with piped stdin into one one-shot message,
    or None when neither is present (→ interactive session).

    Ordering matches `mods`: the instruction (prompt arg) comes first, the piped
    content second, separated by a blank line, then whitespace-trimmed.
    """
    parts = [p for p in (prompt, stdin_text) if p.strip()]
    if not parts:
        return None
    return "\n\n".join(parts).strip()


def _unlock_cipher(cfg: Config) -> crypto.Cipher:
    """Unlock the encryption key, exiting with a friendly error on failure."""
    try:
        return crypto.unlock(cfg.encryption)
    except crypto.CryptoError as e:
        typer.echo(f"Could not unlock encryption key: {e}", err=True)
        raise typer.Exit(1) from e


def _run_chat(
    spec: str,
    *,
    no_record: bool = False,
    oneshot: str | None = None,
    cfg: Config | None = None,
    cipher: crypto.Cipher | None = None,
) -> None:
    """Chat against `<provider>/<model>`. Enters the interactive REPL, or runs a
    single `oneshot` prompt (print + exit) when one is given. Exits with a typer
    error if the spec is malformed or the provider isn't configured.

    `no_record=True` (from `otaku -nr` / `otaku <model> -nr`, or
    `[defaults].no_record`) opens the store in read-only mode: the session's
    turns, summaries, and any /history-picker deletes are silently skipped.

    `cfg` / `cipher` let the bare-`otaku` path pass in the already-loaded config
    and already-unlocked cipher; omitted, both are created here (spec resolution
    first, so a bad spec fails fast without a key ceremony).
    """
    if cfg is None:
        cfg = config.load()
    no_record = no_record or cfg.no_record  # [defaults].no_record → every session read-only
    try:
        provider_name, model = _resolve_spec(cfg, spec)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e

    if cipher is None:
        cipher = _unlock_cipher(cfg)

    try:
        store = Store.open(cfg.database_url, cipher, read_only=no_record)
    except Exception as e:
        typer.echo(f"Could not open database {cfg.database_url}: {e}", err=True)
        raise typer.Exit(1) from e

    provider = cfg.providers[provider_name]
    state = State(
        config=cfg,
        provider=provider,
        model=model,
        full_model=f"{provider_name}/{model}",
        verbose=cfg.verbose,
    )
    apply_settings(state, config.settings_for(cfg, model))
    try:
        if oneshot is not None:
            run_oneshot(state, store, oneshot)
        else:
            summary = None
            if cfg.create_summaries and not store.read_only:
                # The worker opens its own Store on its own thread (WAL makes the
                # concurrent write safe); a fresh cipher isn't needed — the DEK is
                # stateless, so the unlocked cipher is shared.
                summary = SummaryWorker(
                    store_factory=lambda: Store.open(cfg.database_url, cipher),
                    idle_seconds=cfg.summary_idle_seconds,
                )
            repl.run(state, store, summary)
    finally:
        store.close()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    no_record: bool = typer.Option(
        False,
        "-nr",
        "--no-record",
        help="Don't save conversation to the database",
    ),
    version: bool = typer.Option(
        False,
        "-v",
        "--version",
        help="Show version information",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["no_record"] = no_record
    if ctx.invoked_subcommand is not None:
        return
    # Bare `otaku` (no MODEL): open the model picker, then start a chat with
    # the chosen model. Cancel in the picker exits without launching the REPL.
    # Crypto is unlocked *before* the picker so interactive KEK ceremonies
    # (passphrase prompt, a slow `command` provider) happen up front instead of
    # after the model is chosen.
    cfg = config.load()
    cipher = _unlock_cipher(cfg)
    spec = _pick_model_spec(cfg)
    if spec is None:
        return
    _run_chat(spec, no_record=no_record, cfg=cfg, cipher=cipher)


@app.command(name="chat", hidden=True, cls=CompactHelpCommand)
def chat_cmd(
    ctx: typer.Context,
    model_spec: str = typer.Argument(
        ..., metavar="MODEL", help="Model to chat with: a bare name or PROVIDER/MODEL"
    ),
    prompt: str = typer.Argument(
        "",
        metavar="[PROMPT]",
        help="One-shot prompt. Combined with any piped stdin (prompt first). "
        "Omit for an interactive session.",
    ),
    no_record: bool = typer.Option(
        False,
        "-nr",
        "--no-record",
        help="Don't save conversation to the database",
    ),
) -> None:
    """Chat against MODEL (a bare model name, resolved across every configured
    provider, or PROVIDER/MODEL). With a PROMPT argument and/or piped stdin, run
    a single one-shot completion — plain output, no REPL — then exit. This is
    the hidden command that a bare `otaku <model>` invocation routes to."""
    oneshot = _compose_oneshot(prompt, _read_stdin())
    _run_chat(
        model_spec,
        no_record=no_record or bool(ctx.obj.get("no_record", False)),
        oneshot=oneshot,
    )


@app.command(name="stop", short_help="Unload a loaded model", cls=CompactHelpCommand)
def stop_cmd(
    model_spec: str = typer.Argument(
        "",
        help="Model name or PROVIDER/MODEL — bare names are resolved against "
        "the loaded set of every provider",
    ),
    all_models: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Unload every loaded model in every configured provider",
    ),
) -> None:
    """Unload a loaded model so its weights leave RAM. Without arguments
    you must pass --all; otherwise specify a model (bare name or
    PROVIDER/MODEL)."""
    cfg = config.load()

    if all_models:
        if model_spec:
            typer.echo("`stop --all` doesn't take a model argument", err=True)
            raise typer.Exit(2)
        _stop_all(cfg)
        return

    if not model_spec:
        typer.echo("usage: otaku stop <model> | otaku stop --all", err=True)
        raise typer.Exit(2)

    try:
        prov_name, model = _resolve_spec(cfg, model_spec, loaded_only=True)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e

    cli = client_for(cfg.providers[prov_name])
    try:
        cli.unload_model(model)
    except NotImplementedError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    except Exception as e:
        typer.echo(f"failed to unload {prov_name}/{model}: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"unloaded {prov_name}/{model}")


def _print_table(
    headers: list[str],
    rows: list[list[str]],
    aligns: list[str] | None = None,
) -> None:
    """Print a column table. `aligns` is a per-column 'l' / 'c' / 'r'
    list (default all left)."""
    aligns = aligns or ["l"] * len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    align_char = {"l": "<", "c": "^", "r": ">"}
    parts = [f"{{:{align_char[a]}{w}}}" for a, w in zip(aligns, widths, strict=True)]
    fmt = "  ".join(parts)
    typer.echo(fmt.format(*headers).rstrip())
    for row in rows:
        typer.echo(fmt.format(*row).rstrip())


@app.command(name="list", short_help="List models across all providers", cls=CompactHelpCommand)
def list_cmd(
    running: bool = typer.Option(
        False,
        "--running",
        "-r",
        help="Show only the models currently loaded in memory",
    ),
) -> None:
    """Show every model exposed by every configured provider, with size, the
    loaded context window (CONTEXT — only populated for loaded models), and a ✓
    in the LOADED column for models currently in memory. With --running, list
    only the loaded models and drop the LOADED column (every row is loaded).
    Providers that error are silently skipped."""
    cfg = config.load()

    def gather(prov_name: str, provider: Provider) -> tuple[str, list[list[str]]] | None:
        cli = client_for(provider)
        try:
            loaded = cli.loaded_models()
        except Exception:
            loaded = set()
        try:
            models = sorted(loaded) if running else cli.list_models(timeout=3.0)
        except Exception:
            return None  # unreachable — excluded from the reachable set below
        try:
            sizes = cli.model_sizes()
        except Exception:
            sizes = {}
        out: list[list[str]] = []
        for m in models:
            is_loaded = m in loaded
            # context_size is only defined for loaded models — skip the probe
            # (and its HTTP round-trip) for the rest.
            ctx = format_context(cli.context_size(m) if is_loaded else None)
            row = [prov_name, m, format_size(sizes.get(m)), ctx]
            if not running:
                row.append("✓" if is_loaded else "")
            out.append(row)
        return prov_name, out

    with probing_notice(cfg.providers):
        results = map_providers(cfg.providers, gather)
    reachable = {r[0] for r in results if r is not None}
    rows = [row for r in results if r is not None for row in r[1]]
    if not rows:
        # --running keeps its terse note (servers are up, nothing is loaded);
        # a bare `list` with nothing at all gets the config diagnosis.
        typer.echo("no running models" if running else unreachable_help(cfg.providers, reachable))
        return
    if running:
        _print_table(["PROVIDER", "MODEL", "SIZE", "CONTEXT"], rows, aligns=["l", "l", "r", "r"])
    else:
        _print_table(
            ["PROVIDER", "MODEL", "SIZE", "CONTEXT", "LOADED"],
            rows,
            aligns=["l", "l", "r", "r", "c"],
        )


def _stop_all(cfg: Config) -> None:
    """Unload every loaded model across every configured provider. Best
    effort — providers that error are skipped with a stderr note."""

    def stop_one(prov_name: str, provider: Provider) -> tuple[list[str], list[str]]:
        cli = client_for(provider)
        try:
            loaded = cli.loaded_models()
        except Exception:
            return [], []
        done: list[str] = []
        errs: list[str] = []
        for model in sorted(loaded):
            try:
                cli.unload_model(model)
            except Exception as e:
                errs.append(f"failed to unload {prov_name}/{model}: {e}")
                continue
            done.append(f"{prov_name}/{model}")
        return done, errs

    with probing_notice(cfg.providers):
        results = map_providers(cfg.providers, stop_one)
    unloaded = [m for done, _ in results for m in done]
    for _, errs in results:
        for err in errs:
            typer.echo(err, err=True)
    if not unloaded:
        typer.echo("nothing was loaded")
        return
    for m in unloaded:
        typer.echo(f"unloaded {m}")
