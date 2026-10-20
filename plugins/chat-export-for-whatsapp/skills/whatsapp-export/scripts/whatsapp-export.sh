#!/usr/bin/env bash
#
# whatsapp-export.sh - export WhatsApp chats to HTML, Markdown and JSON Lines.
#
# Two sources:
#   - WhatsApp for macOS: reads a read-only snapshot of the app's local
#     database (export, update, list, inspect).
#   - WhatsApp's own "Export chat" files from a phone, .zip or .txt (import),
#     on macOS or Linux.
#
# The work is done by the companion script whatsapp_export.py, which needs
# only Python's standard library. It runs through uv when uv is installed
# (uv supplies a pinned Python), or with python3 3.9 or newer.
#
# Usage: whatsapp-export.sh <command> [options]    (run with "help" for details)

set -euo pipefail

readonly VERSION="0.1.0"
readonly WA_PYTHON="${WHATSAPP_EXPORT_PYTHON:-3.12}"
readonly OUT_ROOT="${WHATSAPP_EXPORT_DIR:-$HOME/whatsapp-chats}"
readonly CONTAINER_DEFAULT="$HOME/Library/Group Containers/group.net.whatsapp.WhatsApp.shared"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
readonly PY_SCRIPT="$SCRIPT_DIR/whatsapp_export.py"

# ---------------------------------------------------------------- helpers ---

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '%s\n' "$*" >&2
}

usage() {
  cat <<EOF
whatsapp-export.sh $VERSION - export WhatsApp chats (HTML, Markdown, JSON Lines)

Usage:
  whatsapp-export.sh <command> [options]

Commands:
  doctor              Check that everything is ready (changes nothing)
  list                Show the exact names of the chats in WhatsApp for macOS
  export              Export chats from WhatsApp for macOS to a new folder
  update              Export again and merge in an earlier export
  import FILE...      Convert chats exported on a phone (.zip or .txt)
  inspect             Show the database's tables and columns, without any
                      messages, for bug reports
  version             Show version information
  help                Show this help

Options for export and update:
  --chats "A,B"       Only these chats, comma-separated, names as shown by
                      "list" (default: all chats)
  --source DIR        WhatsApp's data folder, if not in the standard place
  --from DIR          (update only, required) the earlier export to merge

Options for import:
  --me "NAME"         Your name as it appears in the chat, so your messages
                      are marked as yours (recommended)
  --name "NAME"       Chat name (default: taken from the file name; one file)
  --date-order X      dmy, mdy or ymd, if dates were read the wrong way round
  --from DIR          An earlier export to merge in

Options for export, update and import:
  --out DIR           Output folder; must not exist yet
                      (default: $OUT_ROOT/export-<date-time>)
  --since DATE        Only messages from DATE on (e.g. 2026-01-31)
  --until DATE        Only messages up to and including DATE
  --tz ZONE           Time zone, e.g. Europe/Zurich (default: this computer's)
  --no-attachments    Skip copying images, voice notes and files
  --no-data           Skip the machine-readable files (people.jsonl,
                      chats.jsonl, messages.jsonl, manifest.json)

Options for list:
  --ids               Also show WhatsApp IDs, which contain phone numbers
  --source DIR        As above

Environment variables:
  WHATSAPP_EXPORT_DIR     Default output root (now: $OUT_ROOT)
  WHATSAPP_EXPORT_PYTHON  Python version uv should use (now: $WA_PYTHON)

On a Mac, macOS may ask whether this app may access data from other apps the
first time; click Allow. WhatsApp can stay open.

Exports contain your private messages unencrypted. Keep them somewhere safe.
EOF
}

os_name() {
  uname -s
}

whatsapp_is_running() {
  [[ "$(os_name)" == Darwin ]] || return 1
  pgrep -f '/WhatsApp.app/Contents/MacOS/WhatsApp' >/dev/null 2>&1
}

# Runs whatsapp_export.py with uv's pinned Python, or a recent python3.
run_py() {
  if command -v uv >/dev/null 2>&1; then
    uv run --quiet --no-project --python "$WA_PYTHON" -- python "$PY_SCRIPT" "$@"
  elif python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    python3 "$PY_SCRIPT" "$@"
  else
    die "Neither uv nor Python 3.9+ was found. Install uv with:  brew install uv
       (or see https://docs.astral.sh/uv/getting-started/installation/)"
  fi
}

python_status() {
  if command -v uv >/dev/null 2>&1; then
    printf 'ok (uv %s, Python %s)\n' "$(uv --version 2>/dev/null | awk '{print $2}')" "$WA_PYTHON"
  elif python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    printf 'ok (%s)\n' "$(python3 --version 2>&1)"
  else
    printf 'MISSING - install uv with: brew install uv\n'
    return 1
  fi
}

require_mac() {
  [[ "$(os_name)" == Darwin ]] && return 0
  die "Reading WhatsApp's own database needs WhatsApp for macOS, and this is $(os_name).
       On other systems, export the chat on your phone (chat > More > Export chat)
       and convert it with:  whatsapp-export.sh import FILE.zip --me \"Your Name\""
}

require_whatsapp_data() {
  local dir="$1"
  [[ -f "$dir/ChatStorage.sqlite" ]] && return 0
  if [[ -d "$dir" ]] && ! ls "$dir" >/dev/null 2>&1; then
    die "macOS blocked access to $dir.
       Run the command again and click Allow when macOS asks, or give the app
       running this script Full Disk Access (System Settings > Privacy & Security)."
  fi
  die "No WhatsApp for macOS data found in: $dir
       Install WhatsApp from the Mac App Store or whatsapp.com, link it to your
       phone and let it finish syncing, or point to the folder with --source DIR."
}

# Prints the absolute, symlink-free path of DIR even if DIR does not exist
# yet, by resolving its nearest existing parent.
absolute_path() {
  local path="$1" suffix=""
  path="${path/#\~/$HOME}"
  [[ "$path" == /* ]] || path="$PWD/$path"
  while [[ ! -d "$path" ]]; do
    suffix="/$(basename "$path")$suffix"
    path="$(dirname "$path")"
  done
  printf '%s%s\n' "$(cd "$path" && pwd -P)" "$suffix"
}

# Refuses output folders inside this tool's own repository, so private
# messages can never end up in a git commit.
require_out_outside_repo() {
  local out="$1" repo_root
  repo_root="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
  [[ -n "$repo_root" ]] || return 0
  repo_root="$(cd "$repo_root" && pwd -P)"
  case "$out/" in
    "$repo_root"/*) die "Refusing to export into this tool's repository ($repo_root).
       Choose an output folder outside it, e.g. --out ~/whatsapp-chats/my-export" ;;
  esac
}

# --------------------------------------------------------------- commands ---

cmd_doctor() {
  local ok=1 py
  info "whatsapp-export.sh $VERSION"
  info "System:          $(os_name)"
  if py="$(python_status)"; then
    info "Python:          $py"
  else
    info "Python:          $py"
    ok=0
  fi

  if [[ "$(os_name)" == Darwin ]]; then
    if [[ -f "$CONTAINER_DEFAULT/ChatStorage.sqlite" ]]; then
      info "WhatsApp data:   ok ($CONTAINER_DEFAULT)"
    elif [[ -d "$CONTAINER_DEFAULT" ]] && ! ls "$CONTAINER_DEFAULT" >/dev/null 2>&1; then
      info "WhatsApp data:   BLOCKED by macOS - click Allow when asked, or grant Full Disk Access"
      ok=0
    else
      info "WhatsApp data:   NOT FOUND - WhatsApp for macOS isn't set up (phone exports still work)"
      ok=0
    fi
    if whatsapp_is_running; then
      info "WhatsApp app:    running (fine; messages arriving during the export aren't included)"
    else
      info "WhatsApp app:    not running"
    fi
  else
    info "WhatsApp data:   not available on $(os_name); use 'import' with a phone export"
  fi
  info "Output root:     $OUT_ROOT"

  if ((ok)); then
    info "Ready to export."
  else
    info "Fix the items above, then run doctor again."
    return 1
  fi
}

cmd_list() {
  local source="$CONTAINER_DEFAULT" ids=0
  while (($#)); do
    case "$1" in
      --source)
        [[ $# -ge 2 ]] || die "--source needs a folder"
        source="$2"
        shift 2
        ;;
      --ids) ids=1; shift ;;
      *) die "Unknown option for list: $1 (see: whatsapp-export.sh help)" ;;
    esac
  done
  [[ "$source" != "$CONTAINER_DEFAULT" ]] || require_mac
  require_whatsapp_data "$source"
  local args=(list --source "$source")
  ((ids)) && args+=(--ids)
  run_py "${args[@]}"
}

cmd_inspect() {
  local source="$CONTAINER_DEFAULT"
  if [[ "${1:-}" == "--source" ]]; then
    [[ $# -ge 2 ]] || die "--source needs a folder"
    source="$2"
  elif (($#)); then
    die "Unknown option for inspect: $1"
  fi
  [[ "$source" != "$CONTAINER_DEFAULT" ]] || require_mac
  require_whatsapp_data "$source"
  run_py inspect --source "$source"
}

# Shared by export, update and import. $1 is the mode.
cmd_run() {
  local mode="$1"
  shift

  local chats="" out="" since="" until="" source="$CONTAINER_DEFAULT" from=""
  local tz="" me="" name="" order=""
  local attachments=1 data=1
  local files=()

  while (($#)); do
    case "$1" in
      --chats | --out | --since | --until | --source | --from | --tz | --me | --name | --date-order)
        [[ $# -ge 2 ]] || die "$1 needs a value"
        case "$1" in
          --chats) chats="$2" ;;
          --out) out="$2" ;;
          --since) since="$2" ;;
          --until) until="$2" ;;
          --source) source="$2" ;;
          --from) from="$2" ;;
          --tz) tz="$2" ;;
          --me) me="$2" ;;
          --name) name="$2" ;;
          --date-order) order="$2" ;;
        esac
        shift 2
        ;;
      --no-attachments) attachments=0; shift ;;
      --no-data) data=0; shift ;;
      --*) die "Unknown option for $mode: $1 (see: whatsapp-export.sh help)" ;;
      *)
        [[ "$mode" == import ]] || die "Unexpected argument for $mode: $1 (see: whatsapp-export.sh help)"
        files+=("$1")
        shift
        ;;
    esac
  done

  if [[ "$mode" == import ]]; then
    ((${#files[@]})) || die "import needs at least one exported chat (.zip or .txt)"
    [[ -z "$chats" ]] || die "--chats doesn't apply to import; pass the files instead"
  else
    [[ -z "$me$name$order" ]] || die "--me, --name and --date-order only apply to import"
  fi
  if [[ "$mode" == update ]]; then
    [[ -n "$from" ]] || die "update needs --from DIR, the earlier export to merge"
  elif [[ "$mode" == export && -n "$from" ]]; then
    die "--from only works with update and import"
  fi
  if [[ -n "$from" ]]; then
    from="$(absolute_path "$from")"
    [[ -d "$from" ]] || die "Earlier export not found: $from"
  fi

  [[ -n "$out" ]] || out="$OUT_ROOT/export-$(date +%Y-%m-%d_%H%M%S)"
  out="$(absolute_path "$out")"
  [[ ! -e "$out" ]] || die "Output folder already exists: $out
       Choose a new folder with --out, or delete the old one first."
  require_out_outside_repo "$out"
  mkdir -p "$(dirname "$out")"

  local args
  if [[ "$mode" == import ]]; then
    args=(import ${files[@]+"${files[@]}"})
    [[ -n "$me" ]] && args+=(--me "$me")
    [[ -n "$name" ]] && args+=(--name "$name")
    [[ -n "$order" ]] && args+=(--date-order "$order")
  else
    [[ "$source" != "$CONTAINER_DEFAULT" ]] || require_mac
    require_whatsapp_data "$source"
    args=(export --source "$source")
    [[ -n "$chats" ]] && args+=(--chats "$chats")
  fi
  args+=(--out "$out")
  [[ -n "$since" ]] && args+=(--since "$since")
  [[ -n "$until" ]] && args+=(--until "$until")
  [[ -n "$tz" ]] && args+=(--tz "$tz")
  [[ -n "$from" ]] && args+=(--merge-from "$from")
  ((attachments)) || args+=(--no-attachments)
  ((data)) || args+=(--no-data)

  info "Exporting to: $out"
  [[ -n "$from" ]] && info "Merging in earlier export: $from (it stays unchanged)"
  run_py "${args[@]}" >/dev/null

  if [[ ! -d "$out" ]]; then
    info "Nothing was exported."
    return 0
  fi
  info ""
  info "Done. Your export is in: $out"
  info "Open the index.html file in a chat's folder to read it."
  info "Reminder: these files are unencrypted private messages."
}

cmd_version() {
  printf 'whatsapp-export.sh %s\n' "$VERSION"
  printf 'whatsapp_export.py %s\n' "$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$PY_SCRIPT")"
  printf 'Python             %s (via uv, else python3)\n' "$WA_PYTHON"
}

# ------------------------------------------------------------------- main ---

main() {
  local cmd="${1:-help}"
  (($#)) && shift
  case "$cmd" in
    doctor) cmd_doctor "$@" ;;
    list) cmd_list "$@" ;;
    inspect) cmd_inspect "$@" ;;
    export) cmd_run export "$@" ;;
    update) cmd_run update "$@" ;;
    import) cmd_run import "$@" ;;
    version | --version) cmd_version ;;
    help | -h | --help) usage ;;
    *) usage >&2; die "Unknown command: $cmd" ;;
  esac
}

main "$@"
