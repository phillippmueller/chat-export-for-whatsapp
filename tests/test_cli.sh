#!/usr/bin/env bash
#
# Tests for whatsapp-export.sh's argument handling. They never touch real
# WhatsApp data: a fake "uv" records the arguments the script would pass to
# whatsapp_export.py, and a temporary HOME holds a fake data folder.
#
# Run: tests/test_cli.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPT="$REPO_ROOT/plugins/chat-export-for-whatsapp/skills/whatsapp-export/scripts/whatsapp-export.sh"

# Resolve symlinks (macOS temp folders live behind /var -> /private/var).
TMP="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
unset WHATSAPP_EXPORT_DIR WHATSAPP_EXPORT_PYTHON

FAKE_BIN="$TMP/bin"
export HOME="$TMP/home"
export UV_LOG="$TMP/uv.log"
mkdir -p "$FAKE_BIN" "$HOME"

# Fake uv: logs its arguments one per line, prints a chat list for "list",
# and creates the --out folder like the real script would.
cat >"$FAKE_BIN/uv" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then echo "uv 0.0.0-fake"; exit 0; fi
printf '%s\n' "$@" >"$UV_LOG"
prev=""
for arg in "$@"; do
  [[ "$arg" == "list" ]] && { printf 'Anna Muster\tdirect, 6 messages, last 2026-01-01\n'; exit 0; }
  [[ "$prev" == "--out" ]] && mkdir -p "$arg"
  prev="$arg"
done
exit 0
EOF
# Fake pgrep: WhatsApp is never running in tests.
printf '#!/usr/bin/env bash\nexit 1\n' >"$FAKE_BIN/pgrep"
chmod +x "$FAKE_BIN/uv" "$FAKE_BIN/pgrep"
PATH="$FAKE_BIN:/usr/bin:/bin"
export PATH

SRC="$TMP/container"
mkdir -p "$SRC"
: >"$SRC/ChatStorage.sqlite"
: >"$TMP/chat.zip"

passed=0
failed=0

pass() { passed=$((passed + 1)); printf '  ok    %s\n' "$1"; }
fail() {
  failed=$((failed + 1))
  printf '  FAIL  %s\n' "$1"
  [[ -n "${2:-}" ]] && printf '%s\n' "$2" | sed 's/^/        /'
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    local detail="${2:-}"
    detail="${detail//$'\n'/%0A}"
    printf '::error title=Test failed: %s::%s\n' "$1" "$detail"
  fi
  return 0
}

# expect <name> <expected exit: 0|nonzero> <text that output must contain> -- cmd...
expect() {
  local name="$1" want="$2" needle="$3"
  shift 4
  local out code=0
  rm -f "$UV_LOG"
  out="$("$@" 2>&1)" || code=$?
  if [[ "$want" == "0" && $code -ne 0 ]] || [[ "$want" == "nonzero" && $code -eq 0 ]]; then
    fail "$name (exit $code)" "$out"
  elif [[ -n "$needle" && "$out" != *"$needle"* ]]; then
    fail "$name (missing: $needle)" "$out"
  else
    pass "$name"
  fi
}

# log_has <name> <argument the fake uv must have received>
log_has() {
  if [[ -f "$UV_LOG" ]] && grep -qxF -- "$2" "$UV_LOG"; then
    pass "$1"
  else
    fail "$1" "$(cat "$UV_LOG" 2>/dev/null || echo "(no call logged)")"
  fi
}

log_lacks() {
  if [[ -f "$UV_LOG" ]] && grep -qxF -- "$2" "$UV_LOG"; then
    fail "$1" "$(cat "$UV_LOG")"
  else
    pass "$1"
  fi
}

echo "whatsapp-export.sh tests"

expect "help prints usage" 0 "Usage:" -- "$SCRIPT" help
expect "no command prints usage" 0 "Commands:" -- "$SCRIPT"
expect "version" 0 "whatsapp-export.sh" -- "$SCRIPT" version
expect "unknown command fails" nonzero "Unknown command" -- "$SCRIPT" frobnicate
expect "doctor runs" "" "whatsapp-export.sh" -- "$SCRIPT" doctor

expect "list with --source" 0 "Anna Muster" -- "$SCRIPT" list --source "$SRC"
log_has "list calls the list command" "list"
log_has "list pins the Python version" "3.12"
log_lacks "list hides IDs by default" "--ids"
expect "list --ids" 0 "" -- "$SCRIPT" list --source "$SRC" --ids
log_has "list passes --ids" "--ids"
expect "list rejects unknown option" nonzero "Unknown option" -- "$SCRIPT" list --bogus
expect "missing data folder is reported" nonzero "No WhatsApp for macOS data" -- \
  "$SCRIPT" list --source "$TMP/nope"
if [[ "$(uname -s)" != Darwin ]]; then
  expect "desktop commands explain they need a Mac" nonzero "needs WhatsApp for macOS" -- "$SCRIPT" list
fi

expect "export with options" 0 "Done." -- \
  "$SCRIPT" export --source "$SRC" --chats "Anna Muster,Wandergruppe" --since 2026-01-01 \
  --until 2026-03-31 --tz Europe/Zurich --no-attachments
log_has "export runs the export command" "export"
log_has "export passes chats" "Anna Muster,Wandergruppe"
log_has "export passes since" "2026-01-01"
log_has "export passes until" "2026-03-31"
log_has "export passes the time zone" "Europe/Zurich"
log_has "export passes --no-attachments" "--no-attachments"
if grep -q "^$HOME/whatsapp-chats/export-" "$UV_LOG"; then
  pass "export defaults to a dated folder in ~/whatsapp-chats"
else
  fail "export defaults to a dated folder in ~/whatsapp-chats" "$(cat "$UV_LOG")"
fi

expect "export --no-data" 0 "Done." -- "$SCRIPT" export --source "$SRC" --out "$HOME/out-a" --no-data
log_has "export passes --no-data" "--no-data"
expect "export refuses existing folder" nonzero "already exists" -- \
  "$SCRIPT" export --source "$SRC" --out "$HOME/out-a"
expect "export refuses repository folder" nonzero "Refusing to export" -- \
  "$SCRIPT" export --source "$SRC" --out "$REPO_ROOT/exports"
expect "export rejects unknown option" nonzero "Unknown option" -- "$SCRIPT" export --bogus
expect "export rejects --from" nonzero "only works with update" -- \
  "$SCRIPT" export --source "$SRC" --from "$HOME"
expect "export rejects import options" nonzero "only apply to import" -- \
  "$SCRIPT" export --source "$SRC" --me "Philip"
expect "export rejects stray arguments" nonzero "Unexpected argument" -- \
  "$SCRIPT" export --source "$SRC" chat.zip

expect "update needs --from" nonzero "update needs --from" -- "$SCRIPT" update --source "$SRC"
expect "update rejects missing earlier export" nonzero "not found" -- \
  "$SCRIPT" update --source "$SRC" --from "$HOME/nope"
expect "update merges earlier export" 0 "Merging in earlier export" -- \
  "$SCRIPT" update --source "$SRC" --from "$HOME/out-a" --out "$HOME/out-b"
log_has "update passes --merge-from" "--merge-from"
log_has "update passes the earlier folder" "$HOME/out-a"

expect "import needs a file" nonzero "needs at least one" -- "$SCRIPT" import
expect "import with options" 0 "Done." -- \
  "$SCRIPT" import "$TMP/chat.zip" --me "Philip Muster" --name "Familie" --date-order mdy \
  --out "$HOME/out-c"
log_has "import runs the import command" "import"
log_has "import passes the file" "$TMP/chat.zip"
log_has "import passes --me" "Philip Muster"
log_has "import passes --name" "Familie"
log_has "import passes --date-order" "mdy"
log_lacks "import doesn't read the desktop database" "--source"
expect "import rejects --chats" nonzero "doesn't apply to import" -- \
  "$SCRIPT" import "$TMP/chat.zip" --chats "Anna"
expect "import can merge an earlier export" 0 "Merging" -- \
  "$SCRIPT" import "$TMP/chat.zip" --from "$HOME/out-c" --out "$HOME/out-d"

rm "$FAKE_BIN/uv"
cat >"$FAKE_BIN/python3" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$FAKE_BIN/python3"
expect "missing Python is reported" nonzero "Neither uv nor Python" -- \
  "$SCRIPT" list --source "$SRC"

echo
echo "$passed passed, $failed failed"
((failed == 0))
