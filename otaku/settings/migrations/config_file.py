"""The moves over config.toml that need the launch's hands: a password
typed into `[web]` is replaced by its hash. The hashing is injected with
the test for it, as `providers_file.seal_api_keys`' sealer is — this
package may not reach the encryption plane."""

from collections.abc import Callable

from otaku.formatting import toml_scalar
from otaku.settings import row
from otaku.settings.migrations.surgery import Migration, parse, set_key


def hash_plain_password(
    hash_password: Callable[[str], str], is_password_hashed: Callable[[str], bool]
) -> Migration:
    """A migration replacing a plain, non-empty `[web] password` with its
    hash — however it got there: typed by hand, or left plain by a launch
    that could not write the file. An empty one is no password and stays;
    one already hashed is never hashed again, which is what makes this
    safe to rerun at every launch."""

    def apply(text: str) -> str:
        parsed = parse(text)
        web = parsed.get("web") if parsed is not None else None
        typed = web.get("password") if isinstance(web, dict) else None
        if not isinstance(typed, str) or not typed or is_password_hashed(typed):
            return text
        line = row(
            f"password = {toml_scalar(hash_password(typed))}",
            "RECOMMENDED to set when the host is not local; typed in plain text, it is "
            "replaced by its hash at the next launch",
        )
        return set_key("web", "password", line)(text)

    return apply
