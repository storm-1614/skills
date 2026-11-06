#!/usr/bin/env bash
# translate.sh — translate a PDF with pdf2zh_next, preferring the configured Codex CLI.
#
# Engine selection (unless --engine is given):
#   1. codex        — if `codex` CLI is on PATH. Uses the configured Codex model
#                    through a temporary loopback OpenAI-compatible bridge.
#   2. openai       — if OPENAI_API_KEY (or --api-key) is available.
#   3. claudecode   — if `claude` CLI is on PATH.
#   4. siliconflowfree — zero-config fallback (free GLM-4-9B service, no key).
#
# You can force any engine:
#   --engine codex | openai | claudecode | siliconflowfree
#
# Usage:
#   translate.sh <input.pdf> [--output <dir>] [--pages "1-5,8"]
#               [--lang-in en] [--lang-out zh-CN] [--engine codex]
#               [--model <name>] [--base-url <url>] [--api-key <key>]
#               [--qps N] [--pool-max-workers N] [--no-dual] [--no-mono]
#               [-- --any-other-pdf2zh-flag value]
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_PID=""
BRIDGE_LOG=""

cleanup_codex_bridge() {
  if [[ -n "$BRIDGE_PID" ]]; then
    kill "$BRIDGE_PID" 2>/dev/null || true
    wait "$BRIDGE_PID" 2>/dev/null || true
  fi
  if [[ -n "$BRIDGE_LOG" ]]; then
    rm -f "$BRIDGE_LOG"
  fi
}

# --- make sure uv's tool bin dir is on PATH so pdf2zh_next is found ---
UV_BIN_DIR="$HOME/.local/bin"
case ":$PATH:" in
  *":$UV_BIN_DIR:"*) ;;
  *) export PATH="$UV_BIN_DIR:$PATH" ;;
esac

# ----------------------------- argument parsing -----------------------------
INPUT=""
ENGINE=""
MODEL=""
BASE_URL=""
API_KEY=""
OUTPUT=""
PAGES=""
LANG_IN="en"
LANG_OUT="zh-CN"
QPS=""
POOL=""
NO_DUAL=0
NO_MONO=0
PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --engine)        ENGINE="$2"; shift 2 ;;
    --model)         MODEL="$2"; shift 2 ;;
    --base-url)      BASE_URL="$2"; shift 2 ;;
    --api-key)       API_KEY="$2"; shift 2 ;;
    --output)        OUTPUT="$2"; shift 2 ;;
    --pages)         PAGES="$2"; shift 2 ;;
    --lang-in)       LANG_IN="$2"; shift 2 ;;
    --lang-out)      LANG_OUT="$2"; shift 2 ;;
    --qps)           QPS="$2"; shift 2 ;;
    --pool-max-workers) POOL="$2"; shift 2 ;;
    --no-dual)       NO_DUAL=1; shift ;;
    --no-mono)       NO_MONO=1; shift ;;
    --)              shift; while [[ $# -gt 0 ]]; do PASSTHROUGH+=("$1"); shift; done ;;
    -h|--help)      sed -n '2,30p' "$0"; exit 0 ;;
    -*)             echo "Unknown option: $1" >&2; exit 2 ;;
    *)              if [[ -z "$INPUT" ]]; then INPUT="$1"; else echo "Unexpected arg: $1" >&2; exit 2; fi; shift ;;
  esac
done

if [[ -z "$INPUT" ]]; then
  echo "ERROR: no input PDF given." >&2
  echo "Usage: translate.sh <input.pdf> [options]" >&2
  exit 2
fi

# Resolve input to an absolute path and verify it exists.
INPUT="$(cd "$(dirname "$INPUT")" && pwd)/$(basename "$INPUT")"
if [[ ! -f "$INPUT" ]]; then
  echo "ERROR: input file not found: $INPUT" >&2
  exit 1
fi

# Default output dir: an "output" folder next to the input PDF.
if [[ -z "$OUTPUT" ]]; then
  OUTPUT="$(dirname "$INPUT")/output"
fi
mkdir -p "$OUTPUT"

command -v pdf2zh_next >/dev/null 2>&1 || {
  echo "pdf2zh_next not found. Run setup.sh first." >&2
  exit 1
}

# --------------------------- codex config detection ---------------------------
# Reads ~/.codex/config.toml and prints: <base_url> <model> for the active provider.
read_codex_config() {
  local cfg="$HOME/.codex/config.toml"
  [[ -f "$cfg" ]] || return 0
  python3 - "$cfg" <<'PY'
import sys, tomllib
try:
    with open(sys.argv[1], "rb") as f:
        cfg = tomllib.load(f)
except Exception:
    sys.exit(0)
model = cfg.get("model", "")
provider = cfg.get("model_provider", "")
base_url = ""
provs = cfg.get("model_providers", {})
if provider and provider in provs:
    base_url = provs[provider].get("base_url", "")
if base_url or model:
    print(f"{base_url}\t{model}")
PY
}

CODEX_CONFIG_ROW="$(read_codex_config || true)"

# -------------------------------- engine choice -------------------------------
# Detection priority:
#   1. codex        — if `codex` CLI is available
#   2. openai       — if an OpenAI-compatible API key is configured
#   3. claudecode   — if `claude` CLI is available
#   4. siliconflowfree — zero-config fallback
if [[ -z "$ENGINE" ]]; then
  if command -v codex >/dev/null 2>&1; then
    ENGINE="codex"
    [[ -z "$MODEL" ]] && MODEL="$(printf '%s' "$CODEX_CONFIG_ROW" | cut -f2)"
    [[ -z "$MODEL" ]] && MODEL="gpt-6-luna"
  elif [[ -n "${OPENAI_API_KEY:-}" || -n "$API_KEY" ]]; then
    ENGINE="openai"
  elif command -v claude >/dev/null 2>&1; then
    ENGINE="claudecode"
  else
    ENGINE="siliconflowfree"
  fi
fi

# Base command — every engine shares these.
CMD=(pdf2zh_next "$INPUT" --lang-in "$LANG_IN" --lang-out "$LANG_OUT" --output "$OUTPUT" --watermark-output-mode no_watermark)
[[ -n "$PAGES" ]]        && CMD+=(--pages "$PAGES")
[[ -n "$QPS" ]]          && CMD+=(--qps "$QPS")
[[ -n "$POOL" ]]         && CMD+=(--pool-max-workers "$POOL")
(( NO_DUAL == 1 ))       && CMD+=(--no-dual)
(( NO_MONO == 1 ))       && CMD+=(--no-mono)

case "$ENGINE" in
  openai)
    # Resolve base_url: --base-url > OPENAI_BASE_URL env > codex config.
    if [[ -z "$BASE_URL" ]]; then
      BASE_URL="${OPENAI_BASE_URL:-}"
    fi
    if [[ -z "$BASE_URL" ]]; then
      codex_row="$CODEX_CONFIG_ROW"
      if [[ -n "$codex_row" ]]; then
        BASE_URL="$(printf '%s' "$codex_row" | cut -f1)"
        [[ -z "$MODEL" ]] && MODEL="$(printf '%s' "$codex_row" | cut -f2)"
      fi
    fi
    # Resolve model: --model > codex config (already set above) > default.
    [[ -z "$MODEL" ]] && MODEL="gpt-4o-mini"
    # Resolve key: --api-key > OPENAI_API_KEY env.
    KEY="${API_KEY:-${OPENAI_API_KEY:-}}"
    if [[ -z "$KEY" ]]; then
      echo "ERROR: openai engine needs an API key. Set OPENAI_API_KEY or pass --api-key." >&2
      exit 1
    fi
    if [[ -z "$BASE_URL" ]]; then
      echo "ERROR: openai engine needs a base_url. Pass --base-url or configure ~/.codex/config.toml." >&2
      exit 1
    fi
    # Sensible concurrency defaults for a chat-completions gateway if user didn't set them.
    [[ -z "$QPS" ]]   && CMD+=(--qps 10)
    [[ -z "$POOL" ]]  && CMD+=(--pool-max-workers 30)
    CMD+=(--openai --openai-base-url "$BASE_URL" --openai-api-key "$KEY" --openai-model "$MODEL")
    echo "[translate] engine=openai  base_url=$BASE_URL  model=$MODEL"
    ;;

  codex)
    command -v codex >/dev/null 2>&1 || {
      echo "ERROR: codex CLI not found. Install/sign in to Codex CLI or choose another engine." >&2
      exit 1
    }
    command -v python3 >/dev/null 2>&1 || {
      echo "ERROR: python3 is required for the local Codex bridge." >&2
      exit 1
    }
    if [[ -z "$MODEL" ]]; then
      MODEL="$(printf '%s' "$CODEX_CONFIG_ROW" | cut -f2)"
    fi
    [[ -z "$MODEL" ]] && MODEL="gpt-6-luna"
    if [[ -z "$QPS" ]]; then
      QPS=3
      CMD+=(--qps "$QPS")
    fi
    if [[ -z "$POOL" ]]; then
      POOL=3
      CMD+=(--pool-max-workers "$POOL")
    fi

    BRIDGE_PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
    BRIDGE_LOG="$(mktemp "${TMPDIR:-/tmp}/pdf-translate-codex-bridge.XXXXXX.log")"
    python3 "$SCRIPT_DIR/codex_bridge.py" --port "$BRIDGE_PORT" --model "$MODEL" >"$BRIDGE_LOG" 2>&1 &
    BRIDGE_PID=$!
    trap cleanup_codex_bridge EXIT

    BRIDGE_READY=0
    for _ in {1..80}; do
      if python3 - "$BRIDGE_PORT" <<'PY'
import sys, urllib.request
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/v1/models", timeout=1) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
      then
        BRIDGE_READY=1
        break
      fi
      if ! kill -0 "$BRIDGE_PID" 2>/dev/null; then break; fi
      sleep 0.25
    done
    if (( BRIDGE_READY == 0 )); then
      echo "ERROR: local Codex bridge did not start:" >&2
      cat "$BRIDGE_LOG" >&2
      exit 1
    fi

    CMD+=(--openai --openai-base-url "http://127.0.0.1:${BRIDGE_PORT}/v1" --openai-api-key codex-local --openai-model "$MODEL")
    echo "[translate] engine=codex  model=$MODEL  (local loopback bridge; BabelDOC preserves PDF layout)"
    ;;

  claudecode)
    # Uses the `claude` CLI with the model Claude Code is configured to use.
    # `claude_code_model` defaults to "sonnet", which Claude Code resolves to the
    # user's configured model (e.g. via ANTHROPIC_DEFAULT_SONNET_MODEL).
    CC_MODEL="${MODEL:-sonnet}"
    # Keep concurrency low: each segment spawns a `claude -p` subprocess (heavyweight).
    [[ -z "$QPS" ]]   && CMD+=(--qps 4)
    [[ -z "$POOL" ]]  && CMD+=(--pool-max-workers 8)
    CMD+=(--claudecode --claude-code-model "$CC_MODEL")
    echo "[translate] engine=claudecode  model=$CC_MODEL  (subprocess-per-segment; expect slower runs)"
    ;;

  siliconflowfree)
    # No key, no config. Free GLM-4-9B service; picks its own qps/pool from the server.
    CMD+=(--siliconflowfree)
    echo "[translate] engine=siliconflowfree  (free, no key — content routed via project maintainer's server)"
    ;;

  *)
    echo "ERROR: unknown engine '$ENGINE'. Use codex | openai | claudecode | siliconflowfree." >&2
    exit 2
    ;;
esac

# Append any user passthrough flags.
[[ ${#PASSTHROUGH[@]} -gt 0 ]] && CMD+=("${PASSTHROUGH[@]}")

echo "[translate] output dir: $OUTPUT"
echo "[translate] running: ${CMD[*]}"
# Only warn about the one-time model download if the assets aren't cached yet.
BABELDOC_CACHE="$HOME/.cache/babeldoc"
if [[ ! -d "$BABELDOC_CACHE/models" ]] || [[ -z "$(ls -A "$BABELDOC_CACHE/models" 2>/dev/null)" ]]; then
  echo "(first run downloads model assets and will be slow — subsequent runs use cache)"
fi
echo

# Run it. Don't use set -e for the command itself so we can report cleanly.
set +e
"${CMD[@]}"
RC=$?
set -e

echo
if [[ $RC -ne 0 ]]; then
  echo "[translate] FAILED (exit $RC)." >&2
  echo "Choose another engine explicitly if needed: --engine openai, claudecode, or siliconflowfree." >&2
  exit $RC
fi

echo "[translate] done. Generated files:"
ls -1 "$OUTPUT"/*mono.pdf "$OUTPUT"/*dual.pdf 2>/dev/null | sed 's/^/  /' || true
