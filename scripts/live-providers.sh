#!/usr/bin/env bash
# Launch the local engines the live smokes (scenarios/live) need.
#
#   llama-server  :8080   (brew install llama.cpp)
#   koboldcpp     :5001   (the official standalone binary on PATH)
#   LM Studio     :1234   (checked, never launched: `lms server start`
#                          boots the app — enable Settings → Developer →
#                          Local LLM Service to run it headless at login)
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
cleanup() { [ "${#pids[@]}" -gt 0 ] && kill "${pids[@]}" 2>/dev/null || true; }
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
        echo "LM Studio server is down — not starting it (that would open the app)."
        echo "  enable LM Studio → Settings → Developer → Local LLM Service for a"
        echo "  headless server at login; until then its smokes skip themselves."
    fi
else
    echo "LM Studio: no lms CLI on PATH — its smokes will skip"
fi

echo "ollama (:11434) and omlx (:8000) are assumed up, models loaded."
echo "run: pytest scenarios/live -m live    ·    Ctrl+C stops the engines"
wait
