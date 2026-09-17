#!/usr/bin/env bash
# Launch the local engines the live smokes (scenarios/live) need.
#
#   llama-server  :8080   (brew install llama.cpp)
#   koboldcpp     :5001   (the official standalone binary on PATH)
#   LM Studio     :1234   (started if down — headless when Settings →
#                          Developer → Local LLM Service is enabled,
#                          otherwise this boots the app window. On exit
#                          the server stops and, when the wake brought
#                          the service up, the menu-bar resident quits
#                          too; anything already running stays untouched)
#
# Ollama (:11434) and oMLX (:8000) are assumed to be running already,
# each with a model loaded. A reasoning model that sees — Gemma 4 E4B
# Q4_0 (~4.6 GB) with its projector (~560 MB) — is downloaded on first
# use into $OTAKU_LIVE_MODELS_DIR (default ~/models/otaku-live) and
# shared by llama-server and koboldcpp: the thinking smokes need a
# model with a switch, the image smoke one that takes a photo.
# Ctrl+C stops everything this script started.

set -euo pipefail

MODELS_DIR="${OTAKU_LIVE_MODELS_DIR:-$HOME/models/otaku-live}"
GGUF="$MODELS_DIR/gemma-4-E4B-it-Q4_0.gguf"
MMPROJ="$MODELS_DIR/mmproj-gemma-4-E4B-it-Q8_0.gguf"
REPO_URL="https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/resolve/main"

mkdir -p "$MODELS_DIR"
for file in "$GGUF" "$MMPROJ"; do
    if [ ! -f "$file" ]; then
        echo "downloading $(basename "$file") into $MODELS_DIR ..."
        curl -L --fail -o "$file.part" "$REPO_URL/$(basename "$file")"
        mv "$file.part" "$file"
    fi
done

pids=()
started_lms=""
lms_was_down=""
cleanup() {
    [ "${#pids[@]}" -gt 0 ] && kill "${pids[@]}" 2>/dev/null || true
    # `lms server start` detaches — a pid kill never reaches it. Stop it
    # only when this run started it; a server found running is not ours.
    if [ -n "$started_lms" ]; then
        lms server stop >/dev/null 2>&1 || true
        # The wake also left the service resident in the menu bar — quit
        # it only when it was not running before this script. The polite
        # AppleEvent is ignored by the headless service (osascript still
        # reports success), so TERM the process directly; its helpers and
        # the menu-bar icon go with it.
        if [ -n "$lms_was_down" ]; then
            pkill -f "LM Studio.*--run-as-service" 2>/dev/null || true
        fi
    fi
}
trap cleanup EXIT INT TERM

echo "llama-server on :8080 (log: /tmp/otaku-live-llamacpp.log)"
llama-server -m "$GGUF" --mmproj "$MMPROJ" -c 8192 --jinja --host 127.0.0.1 --port 8080 \
    >/tmp/otaku-live-llamacpp.log 2>&1 &
pids+=($!)

echo "koboldcpp on :5001 (log: /tmp/otaku-live-koboldcpp.log)"
koboldcpp --model "$GGUF" --mmproj "$MMPROJ" --host 127.0.0.1 --port 5001 --contextsize 8192 \
    --quiet >/tmp/otaku-live-koboldcpp.log 2>&1 &
pids+=($!)

if command -v lms >/dev/null 2>&1; then
    if lms server status 2>/dev/null | grep -q "is running on port"; then
        echo "LM Studio server is up (chats auto-load its model)"
    else
        pgrep -qf "MacOS/LM Studio" || lms_was_down=1
        echo "LM Studio: starting the server (stopped again on exit)"
        if lms server start; then
            started_lms=1
        else
            echo "  could not start — its smokes will skip"
        fi
    fi
    if curl -s -m 3 http://127.0.0.1:1234/v1/models 2>/dev/null | grep -q "API token is required"; then
        echo "  LM Studio requires an API token (its 0.4.16+ default): disable the"
        echo "  requirement in the app's Developer settings, or create a token there —"
        echo "  export it as LMSTUDIO_API_KEY for the live smokes, and set it as"
        echo "  LM Studio's api key in otaku's model picker for interactive use."
    fi
else
    echo "LM Studio: no lms CLI on PATH — its smokes will skip"
fi

echo "ollama (:11434) and omlx (:8000) are assumed up, models loaded."
echo "run: pytest scenarios/live -m live    ·    Ctrl+C stops the engines"
wait
