# otaku in a container, built and run by compose.yaml: the terminal
# interface, serving the web on port 9600, the state folder in /data, the
# engines left on the host.
FROM python:3.13-slim

# OTAKU_WEB_PORT overrides a port set in config.toml, which would move the
# server off the one compose.yaml maps.
ENV OTAKU_CONFIG_DIR=/data \
    OTAKU_LOCAL_ENGINES_HOST=host.docker.internal \
    OTAKU_WEB_HOST=0.0.0.0 \
    OTAKU_WEB_PORT=9600 \
    OTAKU_TERMINAL_SERVE=on \
    PYTHONUNBUFFERED=1

# The checkout is mounted for the install, never copied in: the image
# carries the installed package alone.
RUN --mount=type=bind,target=/src,rw \
    pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check /src

# A user of its own, owning /data, so a new volume mounted there is its too.
RUN useradd --create-home otaku && mkdir /data && chown otaku:otaku /data
USER otaku
WORKDIR /home/otaku
EXPOSE 9600

CMD ["otaku"]
