#!/usr/bin/env bash
# Assemble the deployable TERMINAL demo: the real terminal frontend on
# Pyodide, xterm.js as the screen — demo/terminal's page and bootstrap
# plus everything they load, all self-hosted so the deployed directory
# is one origin and works offline:
#
#   DEMO_CACHE=<dir> demo/build_terminal.sh <target-dir>
#
# One PIECE of the demos' one procedure, the site repo's demos/build.sh
# (see demo/build_web.sh): the target is always named, and the downloads
# — pyodide, xterm.js, the wheels — are cached where DEMO_CACHE says,
# never inside this repo; delete that directory to refetch. RUN names the
# python that builds otaku's wheel (the otaku environment's).
#
# The page uses SharedArrayBuffer, so wherever it is deployed the two
# isolation headers must ride along on every response under it:
#
#   Cross-Origin-Opener-Policy: same-origin
#   Cross-Origin-Embedder-Policy: require-corp
#
# They are the host's to add — a `_headers` file, a server rule — so
# the build writes none; demo/serve_terminal.py adds the same two
# locally. As with the web demo, whatever else a site lays over the
# page is that site's to add after the build.
#
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:?usage: DEMO_CACHE=<dir> demo/build_terminal.sh <target-dir>}"
CACHE="${DEMO_CACHE:?set DEMO_CACHE to the downloads directory (outside this repo)}"
PYODIDE_VERSION="0.28.2"
PYODIDE_CDN="https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full"
XTERM_VERSION="5.5.0"
FIT_VERSION="0.10.0"
WEBGL_VERSION="0.18.0"
PTK_VERSION="3.0.53"
RUN="${RUN:-python3}"

mkdir -p "$CACHE"
fetch() { # fetch URL [name] — into the cache, once
  local url="$1" name="${2:-$(basename "$1")}"
  if [ ! -f "$CACHE/$name" ]; then
    curl -fsSL "$url" -o "$CACHE/$name.tmp"
    mv -f "$CACHE/$name.tmp" "$CACHE/$name"
  fi
}

rm -rf "$TARGET"
mkdir -p "$TARGET"/{vendor,pyodide,wheels,py}

# --- the page and the bootstrap ---
cp demo/terminal/index.html demo/terminal/main.js demo/terminal/worker.js "$TARGET/"
cp demo/terminal/boot.py demo/terminal/demo_script.py "$TARGET/py/"

# --- xterm.js ---
fetch "https://cdn.jsdelivr.net/npm/@xterm/xterm@${XTERM_VERSION}/lib/xterm.min.js" xterm.js
fetch "https://cdn.jsdelivr.net/npm/@xterm/xterm@${XTERM_VERSION}/css/xterm.min.css" xterm.css
fetch "https://cdn.jsdelivr.net/npm/@xterm/addon-fit@${FIT_VERSION}/lib/addon-fit.min.js" addon-fit.js
fetch "https://cdn.jsdelivr.net/npm/@xterm/addon-webgl@${WEBGL_VERSION}/lib/addon-webgl.min.js" addon-webgl.js
cp "$CACHE/xterm.js" "$CACHE/xterm.css" "$CACHE/addon-fit.js" "$CACHE/addon-webgl.js" "$TARGET/vendor/"

# --- pyodide core ---
for name in pyodide.js pyodide.asm.js pyodide.asm.wasm python_stdlib.zip pyodide-lock.json; do
  fetch "$PYODIDE_CDN/$name"
  cp "$CACHE/$name" "$TARGET/pyodide/"
done

# --- the pyodide packages the demo loads, dependency closure included ---
# plain python3: `conda run` (a RUN candidate) does not forward stdin
PACKAGE_FILES=$(python3 - "$CACHE/pyodide-lock.json" <<'EOF'
import json, sys
lock = json.load(open(sys.argv[1]))
packages = lock["packages"]
want, seen = ["sqlite3", "ssl", "cryptography", "click", "httpx", "httpcore", "idna"], set()
while want:
    name = want.pop()
    key = name.lower().replace("-", "_")
    if key in seen:
        continue
    seen.add(key)
    entry = packages[key]
    print(entry["file_name"])
    want += entry.get("depends", [])
EOF
)
for file in $PACKAGE_FILES; do
  fetch "$PYODIDE_CDN/$file"
  cp "$CACHE/$file" "$TARGET/pyodide/"
done

# --- the app's own wheels ---
if [ ! -f "$CACHE/prompt_toolkit-${PTK_VERSION}-py3-none-any.whl" ]; then
  $RUN -m pip download --no-deps -q -d "$CACHE" "prompt_toolkit==${PTK_VERSION}"
fi
[ -f "$CACHE"/wcwidth-*-py3-none-any.whl ] || $RUN -m pip download --no-deps -q -d "$CACHE" wcwidth
rm -f "$CACHE"/otaku-*-py3-none-any.whl
$RUN -m pip wheel --no-deps -q -w "$CACHE" .
cp "$CACHE/prompt_toolkit-${PTK_VERSION}-py3-none-any.whl" "$CACHE"/wcwidth-*-py3-none-any.whl "$CACHE"/otaku-*-py3-none-any.whl "$TARGET/wheels/"

# main.js names the wheels it hands the worker; keep them true to what landed.
WCWIDTH="$(basename "$(ls "$TARGET"/wheels/wcwidth-*-py3-none-any.whl)")"
OTAKU="$(basename "$(ls "$TARGET"/wheels/otaku-*-py3-none-any.whl)")"
python3 - "$TARGET/main.js" "$WCWIDTH" "$OTAKU" <<'EOF'
import re, sys
from pathlib import Path
page = Path(sys.argv[1])
text = page.read_text()
text = re.sub(r"wcwidth-[^\"]+\.whl", sys.argv[2], text)
text = re.sub(r"otaku-[^\"]+\.whl", sys.argv[3], text)
page.write_text(text)
EOF

echo "built $TARGET ($(find "$TARGET" -type f | wc -l | tr -d ' ') files, $(du -sh "$TARGET" | cut -f1))"
