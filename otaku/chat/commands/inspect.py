"""Inspection commands: /context, /usage, and /info."""

import click

from otaku.chat.state import Session
from otaku.formatting import flatten, format_size, pretty_path, truncate
from otaku.lore import assembler
from otaku.store import Store
from otaku.terminal import DIM, RESET


def cmd_context(session: Session, store: Store, args: list[str]) -> None:
    """Preview the next request: token estimates per part, the system text
    (yours alone), and the transcript exactly as it will be sent. `otaku
    logs` shows what was actually sent; this shows what is about to be."""
    client = session.providers.get_client(session.provider.name)
    prompt = assembler.assemble_story(store, session, client.get_context_size(session.model))
    preview = assembler.render_preview(prompt, dim=DIM, reset=RESET)
    # Long stories make this thousands of lines; page it like `otaku logs`.
    # color=True keeps the dim markers through less (-R).
    click.echo_via_pager(preview, color=True)


def cmd_usage(session: Session, store: Store, args: list[str]) -> None:
    """`/usage` — tokens spent in this story. `/usage all` — every story in
    the database. Grouped by what the tokens were spent on (chat, lore, …),
    then by model."""
    everything = bool(args) and args[0].lower() == "all"
    if not everything and session.story_id is None:
        print("No story yet — send a message first, or use /usage all.")
        return
    rows = store.usage.get_totals(None if everything else session.story_id)
    if not rows:
        print("No recorded usage yet." if everything else "No recorded usage for this story.")
        return

    scope = "all stories" if everything else "this story"
    labels = [f"{r.purpose} · {r.provider}/{r.model}" for r in rows]
    width = max(len(label) for label in labels)
    print(f"Token usage — {scope}:")
    print(f"  {'':<{width}}  {'REQS':>5}  {'PROMPT':>10}  {'REPLY':>10}  {'TOK/S':>7}")
    for label, r in zip(labels, rows, strict=True):
        rate = r.completion_tokens / r.seconds if r.seconds > 0 else 0.0
        print(
            f"  {label:<{width}}  {r.requests:>5,}  {r.prompt_tokens:>10,}  "
            f"{r.completion_tokens:>10,}  {rate:>7.1f}"
        )
    total_p = sum(r.prompt_tokens for r in rows)
    total_c = sum(r.completion_tokens for r in rows)
    total_r = sum(r.requests for r in rows)
    print(
        f"  {'total':<{width}}  {total_r:>5,}  {total_p:>10,}  {total_c:>10,}"
        f"  {'':>7}\n  ({total_p + total_c:,} tokens across {len(rows)} model/purpose pairs)"
    )


def cmd_info(session: Session, store: Store, args: list[str]) -> None:
    """Best-effort dump of everything otaku knows about the active model and
    session. Network-backed fields are silently skipped when the provider
    doesn't expose them or the request fails."""
    client = session.providers.get_client(session.provider.name)
    provider = session.provider

    print(f"State dir: {pretty_path(session.paths.root)}")
    print()
    print(f"Model:    {session.full_model_name}")
    print(f"Backend:  {client.kind} ({provider.url})")
    if provider.api_key:
        print("Auth:     api_key configured")

    # Load state — only meaningful for backends that expose it.
    if client.kind != "openai":
        try:
            loaded: bool | None = session.model in client.get_loaded_models()
        except Exception:
            loaded = None
        if loaded is not None:
            print(f"Loaded:   {'yes' if loaded else 'no'}")

    context = client.get_context_size(session.model)
    if context is not None:
        print(f"Context:  {context}")

    try:
        size = client.get_model_sizes().get(session.model)
    except Exception:
        size = None
    if size is not None and size > 0:
        print(f"Size:     {format_size(size)}")

    if provider.supports_thinking:
        think = session.think if session.think else "default"
        print(f"Thinking: supported, currently {think}")
    else:
        print("Thinking: not supported")

    if provider.keep_alive:
        print(f"Keep-alive: {provider.keep_alive}")

    print()

    user_count = sum(1 for m in session.messages if m.role == "user")
    assistant_count = sum(1 for m in session.messages if m.role == "assistant")
    print(
        f"Context: {len(session.messages)} messages "
        f"({user_count} user, {assistant_count} assistant)"
    )
    if session.story_id is not None:
        label = flatten(truncate(session.story_label(store), 40))
        print(
            f"  story: {label} ({session.story_id})" if label else f"  story: ({session.story_id})"
        )
    if session.system:
        print(f'System: "{session.system}"')
    if session.params:
        rendered = ", ".join(f"{k} = {v}" for k, v in session.params.items())
        print(f"Parameters: {rendered}")
