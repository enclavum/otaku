"""Inspection commands: /context and /info."""

import click

from otaku.chat.state import DIM, RESET, Session
from otaku.formatting import flatten, format_size, truncate
from otaku.lore import assembler
from otaku.store import Store


def cmd_context(session: Session, store: Store, args: list[str]) -> None:
    """Preview the next request: token estimates per part, the system text
    (yours alone), and the transcript exactly as it will be sent. `otaku
    logs` shows what was actually sent; this shows what is about to be."""
    client = session.providers.get_client(session.provider.name)
    prompt = assembler.assemble(
        session.system, session.messages, client.get_context_size(session.model)
    )
    preview = assembler.render_preview(prompt, dim=DIM, reset=RESET)
    # Long stories make this thousands of lines; page it like `otaku logs`.
    # color=True keeps the dim markers through less (-R).
    click.echo_via_pager(preview, color=True)


def cmd_info(session: Session, store: Store, args: list[str]) -> None:
    """Best-effort dump of everything otaku knows about the active model and
    session. Network-backed fields are silently skipped when the provider
    doesn't expose them or the request fails."""
    client = session.providers.get_client(session.provider.name)
    provider = session.provider

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
        story = store.stories.get(session.story_id)
        if story is not None:
            label = flatten(truncate(story.title or "untitled", 40))
            print(f"  story: {label} ({session.story_id})")
    if session.system:
        print(f'System: "{session.system}"')
    if session.params:
        rendered = ", ".join(f"{k} = {v}" for k, v in session.params.items())
        print(f"Parameters: {rendered}")
