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
# each with a model loaded. A small GGUF (~490 MB, Qwen2.5 0.5B Q4_K_M)
# is downloaded on first use into $OTAKU_LIVE_MODELS_DIR (default
# ~/models/otaku-live) and shared by llama-server and koboldcpp.
# Ctrl+C stops everything this script started.

set -euo pipefail

MODELS_DIR="${OTAKU_LIVE_MODELS_DIR:-$HOME/models/otaku-live}"
GGUF="$MODELS_DIR/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"
GGUF_URL="https://huggingface.co/bartowski/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"

mkdir -p "$MODELS_DIR"
if [ ! -f "$GGUF" ]; then
    echo "downloading the small model (~490 MB) into $MODELS_DIR ..."
    curl -L --fail -o "$GGUF.part" "$GGUF_URL"
    mv "$GGUF.part" "$GGUF"
fi

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
llama-server -m "$GGUF" -c 4096 --host 127.0.0.1 --port 8080 \
    >/tmp/otaku-live-llamacpp.log 2>&1 &
pids+=($!)

echo "koboldcpp on :5001 (log: /tmp/otaku-live-koboldcpp.log)"
koboldcpp --model "$GGUF" --host 127.0.0.1 --port 5001 --contextsize 4096 --quiet \
    >/tmp/otaku-live-koboldcpp.log 2>&1 &
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
