"""The names of the environment variables otaku reads, in one place every
layer may import; what a value means stays with the module that reads it."""

# otaku's home folder: the first one set names it; OTAKU_CONFIG_DIR is the
# older name (`cli.resolve_root`).
HOME = ("OTAKU_HOME", "OTAKU_DATA_DIR", "OTAKU_CONFIG_DIR")
# The web interface's host, port, sign-in password and https (on/off), over
# config.toml's (`backend.app.settings.services`); `otaku serve --host`,
# `--any-host` and `--port` set the first two for their run.
WEB_HOST = "OTAKU_WEB_HOST"
WEB_PORT = "OTAKU_WEB_PORT"
WEB_PASSWORD = "OTAKU_WEB_PASSWORD"
WEB_HTTPS = "OTAKU_WEB_HTTPS"
# Whether the terminal serves the web interface beside it (on/off), over
# config.toml's.
TERMINAL_SERVE = "OTAKU_TERMINAL_SERVE"
# The machine the local engines run on, where otaku runs apart from them —
# in a container (the Dockerfile sets it).
LOCAL_ENGINES_HOST = "OTAKU_LOCAL_ENGINES_HOST"
# "1": the banner in colour where stdout is not a terminal.
COLOR = "OTAKU_COLOR"
