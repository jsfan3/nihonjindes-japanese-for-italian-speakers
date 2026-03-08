#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

# =========================
# AivisSpeech Engine + JP-Extra setup (Linux Mint 22)
# - English logs/messages
# - Detailed error logging/report
# - Final command: aivis-jp-tts
# =========================

# ---------- Defaults ----------
SCRIPT_VERSION="1.0.0"
ENGINE_PORT="${AIVIS_PORT:-10101}"
ENGINE_HOST="${AIVIS_HOST:-127.0.0.1}"
ENGINE_URL="http://${ENGINE_HOST}:${ENGINE_PORT}"
ENGINE_CONTAINER_NAME="${AIVIS_CONTAINER_NAME:-aivisspeech-engine}"
USE_GPU="${AIVIS_USE_GPU:-0}"  # 0=CPU, 1=NVIDIA (requires NVIDIA Container Toolkit)
DATA_DIR="${AIVIS_DATA_DIR:-$HOME/.local/share/AivisSpeech-Engine}"
MODELS_DIR="${DATA_DIR}/Models"
BIN_DIR="${HOME}/bin"
RUNNER_PATH="${BIN_DIR}/aivis-jp-tts"
TEST_OUTPUT_DIR="${HOME}/Music"
TEST_PHRASE_DEFAULT="こんにちは。AivisSpeech Engine のセットアップテストです。"

IMAGE_CPU="ghcr.io/aivis-project/aivisspeech-engine:cpu-latest"
IMAGE_GPU="ghcr.io/aivis-project/aivisspeech-engine:nvidia-latest"

# Aivis Cloud API search (public model metadata)
AIVIS_CLOUD_API_BASE="https://api.aivis-project.com/v1"
AIVIS_MODEL_SEARCH_ENDPOINT="${AIVIS_CLOUD_API_BASE}/aivm-models/search"

# If you know an exact AIVMX URL, set this to skip endpoint probing:
# export AIVIS_AIVMX_URL="https://..."
AIVMX_URL_OVERRIDE="${AIVIS_AIVMX_URL:-}"

# If you want strict behavior, set to 0 (default = 1 to maximize one-shot success):
ALLOW_ENGINE_DEFAULT_FALLBACK="${ALLOW_ENGINE_DEFAULT_FALLBACK:-1}"

# Voice selection options (can be overridden by CLI)
VOICE_NAME=""
VOICE_UUID=""
INTERACTIVE_CHOOSE=1
LIST_ONLY=0
SKIP_DEP_INSTALL=0
SKIP_MODEL_DOWNLOAD=0
TEST_PHRASE="$TEST_PHRASE_DEFAULT"

# Logging/reporting
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_ROOT="${HOME}/.local/state/aivis-jp-tts-setup"
RUN_DIR="${LOG_ROOT}/${TIMESTAMP}"
RUN_LOG="${RUN_DIR}/run.log"
ERROR_REPORT="${RUN_DIR}/error-report.txt"
DEBUG_JSON_DIR="${RUN_DIR}/json"
HTTP_ATTEMPTS_LOG="${RUN_DIR}/http-attempts.log"
mkdir -p "$RUN_DIR" "$DEBUG_JSON_DIR"

# Mirror stdout/stderr to log file
exec > >(tee -a "$RUN_LOG")
exec 2> >(tee -a "$RUN_LOG" >&2)

# Globals
declare -a FAILURES=()
declare -a WARNINGS=()
declare -a DOCKER_CMD=("docker")
SELECTED_MODEL_UUID=""
SELECTED_MODEL_NAME=""
SELECTED_SPEAKER_NAME=""
SELECTED_STYLE_NAME=""
SELECTED_LICENSE=""
SELECTED_TIMBRE=""
SELECTED_DOWNLOADS=""
SELECTED_AIVMX_PATH=""
SELECTED_STYLE_ID=""
USED_FALLBACK_ENGINE_MODEL=0

# ---------- Logging helpers ----------
log()   { printf '[INFO] %s\n' "$*"; }
warn()  { printf '[WARN] %s\n' "$*"; WARNINGS+=("$*"); }
err()   { printf '[ERROR] %s\n' "$*" >&2; FAILURES+=("$*"); }
die()   { err "$*"; exit 1; }

run() {
  log "+ $*"
  "$@"
}

# ---------- Error trap ----------
on_err() {
  local exit_code=$?
  local line_no="${1:-unknown}"
  local cmd="${2:-unknown}"
  err "Command failed (exit=${exit_code}) at line ${line_no}: ${cmd}"
  write_error_report || true
  exit "$exit_code"
}
trap 'on_err "${LINENO}" "${BASH_COMMAND}"' ERR

# ---------- Usage ----------
usage() {
  cat <<'EOF'
Usage:
  bash setup-aivis-jp-tts.sh [options]

Options:
  --voice-name "NAME"       Choose voice by exact/partial name (Aivis Cloud model name)
  --voice-uuid "UUID"       Choose voice by Aivis model UUID (exact match)
  --no-interactive          Do not prompt; auto-pick default best female JP-Extra voice
  --list-only               Print candidate voices and exit (no install/start/test)
  --skip-deps-install       Do not install missing apt packages automatically
  --skip-model-download     Skip JP-Extra model download step (not recommended)
  --test-phrase "TEXT"      Custom Japanese phrase for final test
  --gpu                     Use NVIDIA Docker image (requires NVIDIA Container Toolkit)
  --cpu                     Force CPU Docker image (default)
  --strict                  Disable fallback to engine default model if JP-Extra download fails
  -h, --help                Show this help

Environment overrides (optional):
  AIVIS_AIVMX_URL=...       Exact .aivmx URL to download (bypasses endpoint probing)
  AIVIS_PORT=10101          Engine port
  AIVIS_USE_GPU=1           Same as --gpu
  AIVIS_CONTAINER_NAME=...  Docker container name
  AIVIS_DATA_DIR=...        Host data dir (default: ~/.local/share/AivisSpeech-Engine)
EOF
}

# ---------- Parse args ----------
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --voice-name)
        [[ $# -ge 2 ]] || die "--voice-name requires a value"
        VOICE_NAME="$2"; shift 2;;
      --voice-uuid)
        [[ $# -ge 2 ]] || die "--voice-uuid requires a value"
        VOICE_UUID="$2"; shift 2;;
      --no-interactive)
        INTERACTIVE_CHOOSE=0; shift;;
      --list-only)
        LIST_ONLY=1; shift;;
      --skip-deps-install)
        SKIP_DEP_INSTALL=1; shift;;
      --skip-model-download)
        SKIP_MODEL_DOWNLOAD=1; shift;;
      --test-phrase)
        [[ $# -ge 2 ]] || die "--test-phrase requires a value"
        TEST_PHRASE="$2"; shift 2;;
      --gpu)
        USE_GPU=1; shift;;
      --cpu)
        USE_GPU=0; shift;;
      --strict)
        ALLOW_ENGINE_DEFAULT_FALLBACK=0; shift;;
      -h|--help)
        usage; exit 0;;
      *)
        die "Unknown option: $1";;
    esac
  done
}

# ---------- Report writing ----------
write_error_report() {
  {
    echo "=== Aivis JP-TTS Setup Error Report ==="
    echo "Timestamp: $(date -Is)"
    echo "Script version: ${SCRIPT_VERSION}"
    echo "Run dir: ${RUN_DIR}"
    echo
    echo "--- System ---"
    uname -a || true
    echo "User: $(id || true)"
    echo "PWD: $(pwd || true)"
    echo "Shell: ${SHELL:-unknown}"
    echo
    echo "--- Key variables ---"
    echo "ENGINE_URL=${ENGINE_URL}"
    echo "ENGINE_CONTAINER_NAME=${ENGINE_CONTAINER_NAME}"
    echo "USE_GPU=${USE_GPU}"
    echo "DATA_DIR=${DATA_DIR}"
    echo "MODELS_DIR=${MODELS_DIR}"
    echo "RUNNER_PATH=${RUNNER_PATH}"
    echo "SELECTED_MODEL_UUID=${SELECTED_MODEL_UUID}"
    echo "SELECTED_MODEL_NAME=${SELECTED_MODEL_NAME}"
    echo "SELECTED_AIVMX_PATH=${SELECTED_AIVMX_PATH}"
    echo "USED_FALLBACK_ENGINE_MODEL=${USED_FALLBACK_ENGINE_MODEL}"
    echo
    echo "--- Commands availability ---"
    for c in bash curl jq ffmpeg docker systemctl sudo sha256sum; do
      printf '%-10s : ' "$c"
      command -v "$c" || echo "NOT FOUND"
    done
    echo
    echo "--- Versions ---"
    bash --version | head -n1 || true
    curl --version | head -n1 || true
    jq --version || true
    ffmpeg -version | head -n1 || true
    docker --version || true
    echo
    echo "--- Docker ps ---"
    "${DOCKER_CMD[@]}" ps -a || true
    echo
    echo "--- Docker logs (tail) ---"
    "${DOCKER_CMD[@]}" logs --tail 200 "${ENGINE_CONTAINER_NAME}" 2>&1 || true
    echo
    echo "--- HTTP attempts ---"
    [[ -f "${HTTP_ATTEMPTS_LOG}" ]] && cat "${HTTP_ATTEMPTS_LOG}" || echo "(none)"
    echo
    echo "--- Failures recorded ---"
    if [[ ${#FAILURES[@]} -eq 0 ]]; then
      echo "(none)"
    else
      printf ' - %s\n' "${FAILURES[@]}"
    fi
    echo
    echo "--- Warnings recorded ---"
    if [[ ${#WARNINGS[@]} -eq 0 ]]; then
      echo "(none)"
    else
      printf ' - %s\n' "${WARNINGS[@]}"
    fi
    echo
    echo "--- Tail of run log ---"
    tail -n 200 "${RUN_LOG}" 2>/dev/null || true
  } > "${ERROR_REPORT}"
  log "Error report written to: ${ERROR_REPORT}"
}

# ---------- Preconditions ----------
is_mint_or_ubuntu_like() {
  [[ -r /etc/os-release ]] || return 1
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "linuxmint" || "${ID_LIKE:-}" == *"ubuntu"* || "${ID_LIKE:-}" == *"debian"* ]]
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

require_sudo_if_needed() {
  if [[ $EUID -eq 0 ]]; then
    return 0
  fi
  need_cmd sudo || die "sudo is required for automated package installation / Docker setup."
}

check_network_basic() {
  # Consider "reachable" anything that returns an HTTP code (including 401/403/405).
  local urls=(
    "https://github.com"
    "https://ghcr.io"
    "https://api.aivis-project.com"
  )
  for u in "${urls[@]}"; do
    local code
    code="$(curl -sS -L --max-time 10 -o /dev/null -w '%{http_code}' "$u" || true)"
    if [[ "$code" != "000" ]]; then
      log "Reachable (HTTP ${code}): $u"
    else
      warn "Could not reach: $u (this may break setup steps)"
    fi
  done
}

preflight_checks() {
  log "Starting preflight checks..."
  is_mint_or_ubuntu_like || warn "This script is tuned for Linux Mint / Ubuntu / Debian-like systems."
  need_cmd bash || die "bash not found."
  need_cmd curl || warn "curl is missing (will try to install)."
  need_cmd jq || warn "jq is missing (will try to install)."
  need_cmd ffmpeg || warn "ffmpeg is missing (will try to install)."
  need_cmd docker || warn "docker is missing (will try to install)."
  require_sudo_if_needed
  check_network_basic
  mkdir -p "$MODELS_DIR" "$BIN_DIR" "$TEST_OUTPUT_DIR"
  # Best-effort ownership check (Aivis Docker runs as non-root user inside container)
  if [[ ! -O "$DATA_DIR" ]]; then
    warn "Data dir is not owned by current user: ${DATA_DIR}. Docker container may fail to write cache/models."
  fi
}

install_missing_packages() {
  local missing=()
  for c in curl jq ffmpeg docker; do
    case "$c" in
      docker) need_cmd docker || missing+=("docker.io") ;;
      *) need_cmd "$c" || missing+=("$c") ;;
    esac
  done

  if [[ ${#missing[@]} -eq 0 ]]; then
    log "All required packages are already installed."
    return 0
  fi

  if [[ "$SKIP_DEP_INSTALL" -eq 1 ]]; then
    die "Missing packages: ${missing[*]} (and --skip-deps-install was set)"
  fi

  log "Installing missing packages: ${missing[*]}"
  run sudo apt update
  run sudo apt install -y "${missing[@]}"
}

setup_docker_access() {
  if ! need_cmd docker; then
    die "docker command not found after install step."
  fi

  # Start service if systemd exists
  if need_cmd systemctl; then
    if systemctl is-active --quiet docker; then
      log "Docker service is active."
    else
      log "Starting Docker service..."
      run sudo systemctl enable --now docker
    fi
  else
    warn "systemctl not found; cannot manage Docker service automatically."
  fi

  # Add user to docker group (won't affect current shell)
  if [[ $EUID -ne 0 ]]; then
    if id -nG "$USER" | grep -qw docker; then
      log "User is already in docker group."
    else
      warn "User is not in docker group. Adding now (new login required to take effect)."
      run sudo usermod -aG docker "$USER" || warn "Could not add user to docker group."
    fi
  fi

  # Determine command to use right now
  if docker ps >/dev/null 2>&1; then
    DOCKER_CMD=("docker")
    log "Using Docker as current user."
  else
    if sudo docker ps >/dev/null 2>&1; then
      DOCKER_CMD=("sudo" "docker")
      warn "Using 'sudo docker' for this run (docker group not active in current session)."
    else
      die "Docker is installed but not usable (both 'docker ps' and 'sudo docker ps' failed)."
    fi
  fi
}

# ---------- HTTP helpers ----------
http_attempt_log() {
  local msg="[$(date +%H:%M:%S)] $*"
  echo "$msg" >> "$HTTP_ATTEMPTS_LOG"
  echo "$msg" >&2
}

curl_json_to_file() {
  local url="$1" out="$2"
  local code
  http_attempt_log "GET JSON ${url}"
  code="$(curl -sS -L --max-time 30 \
    -H 'Accept: application/json' \
    -o "$out" -w '%{http_code}' \
    "$url" || true)"
  http_attempt_log " -> HTTP ${code}, bytes=$(wc -c < "$out" 2>/dev/null || echo 0)"
  [[ "$code" =~ ^2 ]] || return 1
  jq empty "$out" >/dev/null 2>&1 || return 1
  return 0
}

curl_download_to_file() {
  local url="$1" out="$2"
  local headers="${out}.headers"
  local code
  http_attempt_log "DOWNLOAD ${url}"
  code="$(curl -sS -L --max-time 180 --retry 2 --retry-delay 2 \
    -D "$headers" \
    -o "$out" -w '%{http_code}' \
    "$url" || true)"
  local bytes=0
  [[ -f "$out" ]] && bytes="$(wc -c < "$out" 2>/dev/null || echo 0)"
  local ctype
  ctype="$(grep -i '^content-type:' "$headers" | tail -n1 | cut -d':' -f2- | xargs || true)"
  http_attempt_log " -> HTTP ${code}, bytes=${bytes}, content-type=${ctype:-unknown}"
  [[ "$code" =~ ^2 ]] || return 1
  # Reject obvious HTML/JSON error payloads
  if [[ -f "$out" ]]; then
    if [[ "$bytes" -lt 1048576 ]]; then
      # likely error payload, but log first lines
      head -c 512 "$out" | tr '\n' ' ' >> "$HTTP_ATTEMPTS_LOG" || true
      echo >> "$HTTP_ATTEMPTS_LOG"
    fi
    if grep -a -q -E '(<html|<!DOCTYPE html|{"detail"|{"error"|<Error>)' "$out"; then
      return 1
    fi
  fi
  return 0
}

# ---------- Aivis Cloud model search / selection ----------
fetch_jp_extra_female_candidates() {
  local out="${DEBUG_JSON_DIR}/candidates_raw.json"
  : > "$out"

  # Query multiple female timbre buckets, then merge/dedupe
  local timbres=(YoungFemale YouthfulFemale AdultFemale MiddleAgedFemale ElderlyFemale)
  local tmp_files=()
  local t idx=0

  for t in "${timbres[@]}"; do
    local f="${DEBUG_JSON_DIR}/search_${idx}_${t}.json"
    local url="${AIVIS_MODEL_SEARCH_ENDPOINT}?sort=download&limit=30&page=1&voice_timbres=${t}"
    if curl_json_to_file "$url" "$f"; then
      tmp_files+=("$f")
    else
      warn "Model search query failed for voice_timbres=${t}"
    fi
    idx=$((idx+1))
  done

  [[ ${#tmp_files[@]} -gt 0 ]] || die "All model search queries failed. See ${HTTP_ATTEMPTS_LOG}"

  jq -s '
    map(.aivm_models // []) | add
    | map(select(
        (.visibility // "") == "Public"
        and ((.model_files // []) | any(
          (.model_type // "") == "AIVMX"
          and (.model_architecture // "") == "Style-Bert-VITS2 (JP-Extra)"
        ))
      ))
    | unique_by(.aivm_model_uuid)
    | map(. + {
        best_aivmx_file: (
          (.model_files // [])
          | map(select((.model_type // "") == "AIVMX" and (.model_architecture // "") == "Style-Bert-VITS2 (JP-Extra)"))
          | sort_by(-(.download_count // 0), -(.file_size // 0))
          | .[0]
        )
      })
    | map(select(.best_aivmx_file != null))
    | sort_by(-(.total_download_count // 0), -(.like_count // 0), .name)
  ' "${tmp_files[@]}" > "$out"

  jq empty "$out" >/dev/null
  echo "$out"
}

print_candidate_voices() {
  local json_file="$1"
  echo
  echo "=== Candidate Female JP-Extra Voices (public Aivis models) ==="
  jq -r '
    if length == 0 then
      "No candidates found."
    else
      (["#", "Name", "Timbre", "Downloads", "License", "Hub URL", "Sample audio", "Sample text"] | @tsv),
      (to_entries[] | [
        (.key + 1 | tostring),
        (.value.name // ""),
        (.value.voice_timbre // ""),
        ((.value.total_download_count // 0) | tostring),
        (.value.best_aivmx_file.license_type // ""),
        ("https://hub.aivis-project.com/aivm-models/" + (.value.aivm_model_uuid // "")),
        (.value.speakers[0].styles[0].voice_samples[0].audio_url // ""),
        (.value.speakers[0].styles[0].voice_samples[0].transcript // "")
      ] | @tsv)
    end
  ' "$json_file" | column -t -s $'\t' || cat "$json_file"
  echo
  echo "Tip: copy/paste the Hub URL in your browser to listen to the voice samples."
  echo
}

match_voice_by_name() {
  local json_file="$1" name="$2"
  # Exact case-insensitive first, then contains case-insensitive
  local idx
  idx="$(jq -r --arg name "$name" '
    (to_entries | map(select((.value.name // "" | ascii_downcase) == ($name | ascii_downcase))) | .[0].key) //
    (to_entries | map(select((.value.name // "" | ascii_downcase) | contains($name | ascii_downcase))) | .[0].key) //
    empty
  ' "$json_file")"
  [[ -n "${idx:-}" ]] || return 1
  echo "$idx"
}

match_voice_by_uuid() {
  local json_file="$1" uuid="$2"
  local idx
  idx="$(jq -r --arg u "$uuid" '
    (to_entries | map(select(.value.aivm_model_uuid == $u)) | .[0].key) // empty
  ' "$json_file")"
  [[ -n "${idx:-}" ]] || return 1
  echo "$idx"
}

choose_voice_interactively() {
  local json_file="$1"
  local count
  count="$(jq 'length' "$json_file")"
  [[ "$count" -gt 0 ]] || die "No candidate voices available to choose from."

  if [[ -n "$VOICE_UUID" ]]; then
    local idx
    if idx="$(match_voice_by_uuid "$json_file" "$VOICE_UUID")"; then
      select_candidate_by_index "$json_file" "$idx"
      return 0
    else
      die "Requested voice UUID not found in candidates: ${VOICE_UUID}"
    fi
  fi

  if [[ -n "$VOICE_NAME" ]]; then
    local idx
    if idx="$(match_voice_by_name "$json_file" "$VOICE_NAME")"; then
      select_candidate_by_index "$json_file" "$idx"
      return 0
    else
      warn "Requested voice name not found: ${VOICE_NAME} (will continue with default auto-pick)"
    fi
  fi

  # Default = top candidate by downloads (already sorted)
  local default_idx=0

  if [[ "$INTERACTIVE_CHOOSE" -eq 1 && -t 0 ]]; then
    echo "Type a voice name (or partial name) to select a different voice."
    echo "Press Enter to use the default auto-selected voice."
    printf 'Voice name [%s]: ' "$(jq -r '.[0].name // "default"' "$json_file")"
    local answer=""
    read -r answer || true
    if [[ -n "$answer" ]]; then
      local idx
      if idx="$(match_voice_by_name "$json_file" "$answer")"; then
        select_candidate_by_index "$json_file" "$idx"
        return 0
      else
        warn "Voice name not found: ${answer}. Using default auto-selected voice."
      fi
    fi
  fi

  select_candidate_by_index "$json_file" "$default_idx"
}

select_candidate_by_index() {
  local json_file="$1" idx="$2"

  SELECTED_MODEL_UUID="$(jq -r --argjson i "$idx" '.[ $i ].aivm_model_uuid // empty' "$json_file")"
  SELECTED_MODEL_NAME="$(jq -r --argjson i "$idx" '.[ $i ].name // empty' "$json_file")"
  SELECTED_SPEAKER_NAME="$(jq -r --argjson i "$idx" '.[ $i ].speakers[0].name // .[ $i ].name // empty' "$json_file")"
  SELECTED_STYLE_NAME="$(jq -r --argjson i "$idx" '.[ $i ].speakers[0].styles[0].name // "Normal"' "$json_file")"
  SELECTED_LICENSE="$(jq -r --argjson i "$idx" '.[ $i ].best_aivmx_file.license_type // empty' "$json_file")"
  SELECTED_TIMBRE="$(jq -r --argjson i "$idx" '.[ $i ].voice_timbre // empty' "$json_file")"
  SELECTED_DOWNLOADS="$(jq -r --argjson i "$idx" '.[ $i ].total_download_count // 0' "$json_file")"

  [[ -n "$SELECTED_MODEL_UUID" && -n "$SELECTED_MODEL_NAME" ]] || die "Failed to select a voice candidate."

  log "Selected voice candidate:"
  log "  Name: ${SELECTED_MODEL_NAME}"
  log "  UUID: ${SELECTED_MODEL_UUID}"
  log "  Timbre: ${SELECTED_TIMBRE}"
  log "  License: ${SELECTED_LICENSE}"
  log "  Downloads: ${SELECTED_DOWNLOADS}"
  log "  Speaker (expected in engine): ${SELECTED_SPEAKER_NAME}"
  log "  Default style (local): ${SELECTED_STYLE_NAME}"
}

safe_filename() {
  local s="$1"
  s="${s//\//-}"
  s="${s// /_}"
  s="$(echo "$s" | tr -cd '[:alnum:]_.-一-龯ぁ-ゔァ-ヴー々〆〤（）()')"
  [[ -n "$s" ]] || s="aivis_model"
  echo "$s"
}

# ---------- Model download ----------
download_selected_aivmx() {
  [[ -n "$SELECTED_MODEL_UUID" ]] || die "No selected model UUID."

  mkdir -p "$MODELS_DIR"
  local base_name
  base_name="$(safe_filename "${SELECTED_MODEL_NAME}")-${SELECTED_MODEL_UUID}.aivmx"
  local final_path="${MODELS_DIR}/${base_name}"
  local tmp_path="${final_path}.part"

  if [[ -f "$final_path" && -s "$final_path" ]]; then
    log "Model already present: $final_path"
    SELECTED_AIVMX_PATH="$final_path"
    return 0
  fi

  if [[ "$SKIP_MODEL_DOWNLOAD" -eq 1 ]]; then
    warn "Skipping model download by request (--skip-model-download)."
    return 1
  fi

  local urls=()
  if [[ -n "$AIVMX_URL_OVERRIDE" ]]; then
    urls+=("$AIVMX_URL_OVERRIDE")
    log "Using AIVMX URL override from AIVIS_AIVMX_URL"
  else
    # Endpoint probing (logged). These may change over time.
    urls+=(
      "${AIVIS_CLOUD_API_BASE}/aivm-models/${SELECTED_MODEL_UUID}/aivmx"
      "${AIVIS_CLOUD_API_BASE}/aivm-models/${SELECTED_MODEL_UUID}/aivmx/download"
      "${AIVIS_CLOUD_API_BASE}/aivm-models/${SELECTED_MODEL_UUID}/download?format=aivmx"
      "${AIVIS_CLOUD_API_BASE}/aivm-models/${SELECTED_MODEL_UUID}/download?model_type=AIVMX"
      "${AIVIS_CLOUD_API_BASE}/aivm-models/${SELECTED_MODEL_UUID}/files/aivmx"
      "${AIVIS_CLOUD_API_BASE}/aivm-models/${SELECTED_MODEL_UUID}/files/aivmx/download"
    )
  fi

  local u ok=0
  rm -f "$tmp_path" "$tmp_path.headers" 2>/dev/null || true

  for u in "${urls[@]}"; do
    rm -f "$tmp_path" "$tmp_path.headers" 2>/dev/null || true
    if curl_download_to_file "$u" "$tmp_path"; then
      local size
      size="$(wc -c < "$tmp_path" 2>/dev/null || echo 0)"
      if [[ "$size" -ge 50000000 ]]; then
        mv -f "$tmp_path" "$final_path"
        ok=1
        log "Downloaded AIVMX model: $final_path ($(numfmt --to=iec-i --suffix=B "$size" 2>/dev/null || echo "${size} bytes"))"
        break
      else
        warn "Downloaded file is too small to be a model (${size} bytes). Trying next endpoint..."
      fi
    else
      warn "Download attempt failed for endpoint: $u"
    fi
  done

  if [[ "$ok" -ne 1 ]]; then
    err "Automatic AIVMX download failed for model UUID: ${SELECTED_MODEL_UUID}"
    err "See HTTP attempt log: ${HTTP_ATTEMPTS_LOG}"
    return 1
  fi

  if need_cmd sha256sum; then
    log "SHA256: $(sha256sum "$final_path" | awk '{print $1}')"
  fi

  SELECTED_AIVMX_PATH="$final_path"
  return 0
}

# ---------- Engine management ----------
engine_image() {
  if [[ "$USE_GPU" == "1" ]]; then
    echo "$IMAGE_GPU"
  else
    echo "$IMAGE_CPU"
  fi
}

start_engine_container() {
  mkdir -p "$MODELS_DIR"

  local image
  image="$(engine_image)"

  # Pull image (best effort retries)
  log "Pulling engine image: $image"
  "${DOCKER_CMD[@]}" pull "$image"

  # If container exists, start/recreate if image mode changed
  if "${DOCKER_CMD[@]}" ps -a --format '{{.Names}}' | grep -qx "$ENGINE_CONTAINER_NAME"; then
    if "${DOCKER_CMD[@]}" ps --format '{{.Names}}' | grep -qx "$ENGINE_CONTAINER_NAME"; then
      log "Engine container already running: $ENGINE_CONTAINER_NAME"
      return 0
    fi
    log "Starting existing engine container: $ENGINE_CONTAINER_NAME"
    "${DOCKER_CMD[@]}" start "$ENGINE_CONTAINER_NAME" >/dev/null
    return 0
  fi

  local -a gpu_args=()
  if [[ "$USE_GPU" == "1" ]]; then
    gpu_args=(--gpus all)
  fi

  # IMPORTANT: mount host ~/.local/share/AivisSpeech-Engine -> container /home/user/.local/share/AivisSpeech-Engine-Dev
  log "Creating and starting engine container..."
  "${DOCKER_CMD[@]}" run -d \
    --name "$ENGINE_CONTAINER_NAME" \
    --restart unless-stopped \
    "${gpu_args[@]}" \
    -p "${ENGINE_PORT}:10101" \
    -v "${DATA_DIR}:/home/user/.local/share/AivisSpeech-Engine-Dev" \
    "$image" >/dev/null

  log "Engine container started: $ENGINE_CONTAINER_NAME"
}

wait_engine_ready() {
  log "Waiting for AivisSpeech Engine to become ready on ${ENGINE_URL} ..."
  local attempts=1200
  local i
  for ((i=1; i<=attempts; i++)); do
    if curl -fsS "${ENGINE_URL}/speakers" >/dev/null 2>&1; then
      log "Engine is ready."
      return 0
    fi
    if (( i % 15 == 0 )); then
      log "Still waiting... (attempt ${i}/${attempts})"
      "${DOCKER_CMD[@]}" logs --tail 20 "$ENGINE_CONTAINER_NAME" 2>&1 || true
    fi
    sleep 2
  done

  "${DOCKER_CMD[@]}" logs --tail 200 "$ENGINE_CONTAINER_NAME" 2>&1 || true
  die "Engine did not become ready in time. First start can be slow due model/BERT downloads."
}

dump_engine_speakers_json() {
  local out="${DEBUG_JSON_DIR}/engine_speakers.json"
  curl -fsS "${ENGINE_URL}/speakers" > "$out"
  jq empty "$out" >/dev/null
  echo "$out"
}

print_engine_voices() {
  local speakers_json="$1"
  echo
  echo "=== Voices currently available in local AivisSpeech Engine ==="
  jq -r '
    if length == 0 then
      "No local voices available in engine."
    else
      (["STYLE_ID", "Speaker", "Style"] | @tsv),
      (.[] as $sp | ($sp.styles // [])[] | [(.id|tostring), ($sp.name // ""), (.name // "")] | @tsv)
    end
  ' "$speakers_json" | column -t -s $'\t' || cat "$speakers_json"
  echo
}

resolve_style_id_for_selected_voice() {
  local speakers_json="$1"

  # Try exact speaker name from selected Aivis model metadata
  if [[ -n "${SELECTED_SPEAKER_NAME:-}" ]]; then
    SELECTED_STYLE_ID="$(jq -r --arg n "$SELECTED_SPEAKER_NAME" '
      .[] | select(.name == $n) | (.styles[0].id // empty)
    ' "$speakers_json" | head -n1)"
  fi

  # Fallback: try model/display name match
  if [[ -z "${SELECTED_STYLE_ID:-}" && -n "${SELECTED_MODEL_NAME:-}" ]]; then
    SELECTED_STYLE_ID="$(jq -r --arg n "$SELECTED_MODEL_NAME" '
      .[] | select((.name // "") == $n) | (.styles[0].id // empty)
    ' "$speakers_json" | head -n1)"
  fi

  # Fallback: case-insensitive contains
  if [[ -z "${SELECTED_STYLE_ID:-}" && -n "${SELECTED_MODEL_NAME:-}" ]]; then
    SELECTED_STYLE_ID="$(jq -r --arg n "$SELECTED_MODEL_NAME" '
      .[] | select(((.name // "") | ascii_downcase) | contains(($n | ascii_downcase))) | (.styles[0].id // empty)
    ' "$speakers_json" | head -n1)"
  fi

  # Last fallback: first available style
  if [[ -z "${SELECTED_STYLE_ID:-}" ]]; then
    SELECTED_STYLE_ID="$(jq -r '([.[] | .styles[]?.id] | .[0]) // empty' "$speakers_json")"
    if [[ -n "${SELECTED_STYLE_ID:-}" ]]; then
      warn "Could not map selected voice by name to engine speakers. Using the first available local style instead."
      USED_FALLBACK_ENGINE_MODEL=1
    fi
  fi

  [[ -n "${SELECTED_STYLE_ID:-}" ]] || die "No style ID available in engine (/speakers is empty)."
  log "Selected local engine style ID for synthesis test: ${SELECTED_STYLE_ID}"
}

# ---------- Runner script ----------
install_runner_script() {
  mkdir -p "$BIN_DIR"
  cat > "$RUNNER_PATH" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ENGINE_HOST="${AIVIS_HOST:-127.0.0.1}"
ENGINE_PORT="${AIVIS_PORT:-10101}"
ENGINE_URL="http://${ENGINE_HOST}:${ENGINE_PORT}"
DATA_DIR="${AIVIS_DATA_DIR:-$HOME/.local/share/AivisSpeech-Engine}"
CONTAINER_NAME="${AIVIS_CONTAINER_NAME:-aivisspeech-engine}"
USE_GPU="${AIVIS_USE_GPU:-0}"
IMAGE_CPU="ghcr.io/aivis-project/aivisspeech-engine:cpu-latest"
IMAGE_GPU="ghcr.io/aivis-project/aivisspeech-engine:nvidia-latest"

STYLE_ID="${AIVIS_STYLE_ID:-}"
VOICE_NAME="${AIVIS_VOICE_NAME:-}"
SPEED="${AIVIS_SPEED:-0.7}"   # default 0.7 unless explicitly provided

log()  { printf '[aivis-jp-tts] %s\n' "$*"; }
die()  { printf '[aivis-jp-tts][ERROR] %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1; }

docker_cmd_init() {
  if docker ps >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
  elif sudo docker ps >/dev/null 2>&1; then
    DOCKER_CMD=(sudo docker)
  else
    die "Docker is not usable (tried docker / sudo docker)."
  fi
}

start_engine_if_needed() {
  mkdir -p "${DATA_DIR}/Models"
  docker_cmd_init

  if "${DOCKER_CMD[@]}" ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    return 0
  fi
  if "${DOCKER_CMD[@]}" ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    "${DOCKER_CMD[@]}" start "$CONTAINER_NAME" >/dev/null
    return 0
  fi

  local image="$IMAGE_CPU"
  local gpu_args=()
  if [[ "$USE_GPU" == "1" ]]; then
    image="$IMAGE_GPU"
    gpu_args=(--gpus all)
  fi

  "${DOCKER_CMD[@]}" pull "$image" >/dev/null
  "${DOCKER_CMD[@]}" run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    "${gpu_args[@]}" \
    -p "${ENGINE_PORT}:10101" \
    -v "${DATA_DIR}:/home/user/.local/share/AivisSpeech-Engine-Dev" \
    "$image" >/dev/null
}

wait_engine_ready() {
  for _ in $(seq 1 180); do
    if curl -fsS "${ENGINE_URL}/speakers" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  die "Engine did not become ready at ${ENGINE_URL}."
}

list_voices() {
  local json
  json="$(curl -fsS "${ENGINE_URL}/speakers")"
  if [[ "$(echo "$json" | jq 'length')" -eq 0 ]]; then
    die "No voices available in engine. Add a .aivmx model to ${DATA_DIR}/Models"
  fi
  echo "STYLE_ID | SPEAKER | STYLE"
  echo "$json" | jq -r '.[] as $sp | ($sp.styles // [])[] | "\(.id) | \($sp.name) | \(.name)"'
}

resolve_style_id() {
  local json="$1"

  if [[ -n "${STYLE_ID}" ]]; then
    return 0
  fi

  if [[ -n "${VOICE_NAME}" ]]; then
    STYLE_ID="$(echo "$json" | jq -r --arg n "$VOICE_NAME" '
      (
        [.[] | select((.name // "") == $n) | (.styles[0].id // empty)] |
        .[0]
      ) //
      (
        [.[] | select(((.name // "") | ascii_downcase) == ($n | ascii_downcase)) | (.styles[0].id // empty)] |
        .[0]
      ) //
      (
        [.[] | select(((.name // "") | ascii_downcase) | contains($n | ascii_downcase)) | (.styles[0].id // empty)] |
        .[0]
      ) //
      empty
    ')"
    if [[ -z "${STYLE_ID}" ]]; then
      die "Voice name not found in engine: ${VOICE_NAME}. Run --list-voices."
    fi
  fi

  if [[ -z "${STYLE_ID}" ]]; then
    STYLE_ID="$(echo "$json" | jq -r '([.[] | .styles[]?.id] | .[0]) // empty')"
  fi

  [[ -n "${STYLE_ID}" ]] || die "Could not resolve a style ID."
}

synthesize_to_mp3() {
  local text="$1"
  local out_mp3="$2"

  local txtfile queryfile wavfile
  AIVIS_TMPDIR="$(mktemp -d)"
  trap 'rm -rf "${AIVIS_TMPDIR:-}"' EXIT

  txtfile="${AIVIS_TMPDIR}/input.txt"
  queryfile="${AIVIS_TMPDIR}/query.json"
  wavfile="${AIVIS_TMPDIR}/audio.wav"

  printf '%s' "$text" > "$txtfile"
  mkdir -p "$(dirname "$out_mp3")"

  curl -fsS -X POST "${ENGINE_URL}/audio_query?speaker=${STYLE_ID}" \
    --get --data-urlencode "text@${txtfile}" \
    > "$queryfile"

  jq --argjson s "$SPEED" '.speedScale=$s' "$queryfile" > "${queryfile}.tmp" && mv "${queryfile}.tmp" "$queryfile"

  curl -fsS -H "Content-Type: application/json" \
    -X POST \
    -d @"$queryfile" \
    "${ENGINE_URL}/synthesis?speaker=${STYLE_ID}" \
    > "$wavfile"

  ffmpeg -y -loglevel error -i "$wavfile" -codec:a libmp3lame -q:a 2 "$out_mp3"

  log "OK -> ${out_mp3}"
  log "Style ID: ${STYLE_ID}"
}

usage() {
  cat <<'EOT'
Usage:
  aivis-jp-tts "Japanese text" /path/output.mp3
  aivis-jp-tts --list-voices
  aivis-jp-tts --voice-name "Speaker Name" "Japanese text" /path/output.mp3
  aivis-jp-tts --speed 0.7 "Japanese text" /path/output.mp3

Options:
  --list-voices                Print local engine voices
  --voice-name "NAME"          Choose speaker by name (exact/partial)
  --style-id N                 Choose style directly
  --speed N                    Set speech speedScale (0.5..2.0). Default: 0.7
  -h, --help                   Show help

Env (optional):
  AIVIS_HOST=127.0.0.1
  AIVIS_PORT=10101
  AIVIS_USE_GPU=1
  AIVIS_STYLE_ID=...
  AIVIS_VOICE_NAME="..."
EOT
}

main() {
  need_cmd curl || die "curl not found"
  need_cmd jq || die "jq not found"
  need_cmd awk || die "awk not found"
  need_cmd ffmpeg || die "ffmpeg not found"
  need_cmd docker || need_cmd sudo || die "docker/sudo not found"

  local list_only=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --list-voices) list_only=1; shift ;;
      --voice-name) [[ $# -ge 2 ]] || die "--voice-name requires a value"; VOICE_NAME="$2"; shift 2 ;;
      --style-id) [[ $# -ge 2 ]] || die "--style-id requires a value"; STYLE_ID="$2"; shift 2 ;;
      --speed) [[ $# -ge 2 ]] || die "--speed requires a value"; SPEED="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) break ;;
    esac
  done

  # Validate speed range (Aivis: 0.5..2.0)
  if ! awk "BEGIN{exit !($SPEED >= 0.5 && $SPEED <= 2.0)}"; then
    die "--speed must be between 0.5 and 2.0 (got: $SPEED)"
  fi

  start_engine_if_needed
  wait_engine_ready

  local speakers_json
  speakers_json="$(curl -fsS "${ENGINE_URL}/speakers")"

  if [[ "$list_only" -eq 1 ]]; then
    list_voices
    exit 0
  fi

  [[ $# -ge 2 ]] || { usage; exit 1; }

  resolve_style_id "$speakers_json"
  synthesize_to_mp3 "$1" "$2"
}

main "$@"
EOF

  chmod +x "$RUNNER_PATH"
  log "Installed runner command: ${RUNNER_PATH}"

  if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    warn "${BIN_DIR} is not in PATH for this shell."
    if [[ -f "${HOME}/.bashrc" ]] && ! grep -Fq 'export PATH="$HOME/bin:$PATH"' "${HOME}/.bashrc"; then
      echo 'export PATH="$HOME/bin:$PATH"' >> "${HOME}/.bashrc"
      log "Added ${BIN_DIR} to PATH in ~/.bashrc"
    fi
  fi
}

# ---------- Final synthesis test ----------
run_final_test() {
  mkdir -p "$TEST_OUTPUT_DIR"
  local out_mp3="${TEST_OUTPUT_DIR}/aivis-setup-test-${TIMESTAMP}.mp3"

  local txtfile queryfile wavfile
  AIVIS_SETUP_TMPDIR="$(mktemp -d)"
  trap 'rm -rf "${AIVIS_SETUP_TMPDIR:-}"' RETURN

  txtfile="${AIVIS_SETUP_TMPDIR}/input.txt"
  queryfile="${AIVIS_SETUP_TMPDIR}/query.json"
  wavfile="${AIVIS_SETUP_TMPDIR}/audio.wav"

  printf '%s' "$TEST_PHRASE" > "$txtfile"

  log "Running final synthesis test..."
  log "Test phrase: ${TEST_PHRASE}"

  curl -fsS -X POST "${ENGINE_URL}/audio_query?speaker=${SELECTED_STYLE_ID}" \
    --get --data-urlencode "text@${txtfile}" \
    > "$queryfile"

  jq '.speedScale=0.7' "$queryfile" > "${queryfile}.tmp" && mv "${queryfile}.tmp" "$queryfile"

  curl -fsS -H "Content-Type: application/json" \
    -X POST \
    -d @"$queryfile" \
    "${ENGINE_URL}/synthesis?speaker=${SELECTED_STYLE_ID}" \
    > "$wavfile"

  ffmpeg -y -loglevel error -i "$wavfile" -codec:a libmp3lame -q:a 2 "$out_mp3"

  [[ -s "$out_mp3" ]] || die "Final test failed: output MP3 was not created."
  log "Final test OK. MP3 generated: $out_mp3"
}

print_final_instructions() {
  local speakers_json="${DEBUG_JSON_DIR}/engine_speakers.json"
  echo
  echo "=================================================================="
  echo "SETUP COMPLETED"
  echo "=================================================================="
  echo
  echo "Run logs:"
  echo "  ${RUN_LOG}"
  echo
  echo "If a step failed, the detailed error report is here:"
  echo "  ${ERROR_REPORT}"
  echo
  echo "Installed command:"
  echo "  ${RUNNER_PATH}"
  echo
  echo "Quick usage:"
  echo "  aivis-jp-tts --list-voices"
  echo "  aivis-jp-tts \"こんにちは。テストです。\" ~/Music/hello.mp3"
  echo "  aivis-jp-tts --voice-name \"<speaker name>\" \"おはようございます。\" ~/Music/ohayo.mp3"
  echo
  echo "Optional environment variables:"
  echo "  export AIVIS_USE_GPU=1               # if NVIDIA Docker is configured"
  echo "  export AIVIS_PORT=${ENGINE_PORT}"
  echo "  export AIVIS_VOICE_NAME=\"<speaker name>\""
  echo "  export AIVIS_STYLE_ID=<style_id>"
  echo
  echo "Notes:"
  echo "  - Local engine URL: ${ENGINE_URL}"
  echo "  - Models directory: ${MODELS_DIR}"
  if [[ -n "${SELECTED_MODEL_NAME:-}" ]]; then
    echo "  - Selected model candidate: ${SELECTED_MODEL_NAME} (${SELECTED_MODEL_UUID})"
  fi
  if [[ -n "${SELECTED_AIVMX_PATH:-}" ]]; then
    echo "  - Downloaded model file: ${SELECTED_AIVMX_PATH}"
  fi
  if [[ "${USED_FALLBACK_ENGINE_MODEL}" -eq 1 ]]; then
    echo "  - WARNING: Test used a fallback local engine voice (name matching failed or JP-Extra download unavailable)."
  fi
  echo
  echo "Local voices currently loaded in the engine:"
  if [[ -f "$speakers_json" ]]; then
    jq -r '.[] as $sp | ($sp.styles // [])[] | "  - \($sp.name) / \(.name) (style_id=\(.id))"' "$speakers_json" || true
  else
    echo "  (speakers snapshot not available)"
  fi
  echo
  echo "If your current shell does not recognize 'aivis-jp-tts', run:"
  echo "  source ~/.bashrc"
  echo
}

# ---------- Main flow ----------
main() {
  parse_args "$@"

  log "Aivis JP-TTS setup script version ${SCRIPT_VERSION}"
  log "Run directory: ${RUN_DIR}"

  preflight_checks
  install_missing_packages
  setup_docker_access

  # Query voice candidates first (so user can choose by name before download)
  local candidates_json
  candidates_json="$(fetch_jp_extra_female_candidates)"
  cp -f "$candidates_json" "${DEBUG_JSON_DIR}/candidates_final.json"

  print_candidate_voices "$candidates_json"

  if [[ "$LIST_ONLY" -eq 1 ]]; then
    log "--list-only requested. Exiting after candidate list."
    exit 0
  fi

  choose_voice_interactively "$candidates_json"

  # Try to download selected JP-Extra AIVMX model
  local model_download_ok=1
  if ! download_selected_aivmx; then
    model_download_ok=0
    if [[ "$ALLOW_ENGINE_DEFAULT_FALLBACK" -eq 1 ]]; then
      warn "JP-Extra model download failed; continuing with engine startup and local voice fallback if available."
    else
      die "JP-Extra model download failed and strict mode is enabled."
    fi
  fi

  # Start engine and wait
  start_engine_container
  wait_engine_ready

  local speakers_json
  speakers_json="$(dump_engine_speakers_json)"
  print_engine_voices "$speakers_json"

  # If the selected model was downloaded, try to map its speaker name to a local style ID.
  # Otherwise fallback to first available local voice (if engine has a default).
  if [[ "$model_download_ok" -eq 0 ]]; then
    USED_FALLBACK_ENGINE_MODEL=1
  fi

  resolve_style_id_for_selected_voice "$speakers_json"

  # Install final reusable command
  install_runner_script

  # Final test
  run_final_test

  # Print usage instructions
  print_final_instructions
}

main "$@"
