#!/usr/bin/env bash
#
# Try the tool without touching real messages: builds fake WhatsApp data with
# invented people and messages, then exports it with the real script, both
# from the "desktop database" and from "phone exports".
#
# Usage: tests/demo.sh [OUTPUT_FOLDER]     (default: ~/whatsapp-export-demo)
#
# Nothing reads your own WhatsApp data.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPT="$REPO_ROOT/plugins/chat-export-for-whatsapp/skills/whatsapp-export/scripts/whatsapp-export.sh"
OUT="${1:-$HOME/whatsapp-export-demo}"

[[ ! -e "$OUT" ]] || { echo "$OUT already exists. Remove it or pass another folder." >&2; exit 1; }

mkdir -p "$OUT"
echo "Creating fake WhatsApp data in $OUT/fake-data"
python3 "$REPO_ROOT/tests/make_fake_whatsapp.py" "$OUT/fake-data" >/dev/null

bash "$SCRIPT" export --source "$OUT/fake-data/container" --out "$OUT/desktop-export"
bash "$SCRIPT" import "$OUT/fake-data/phone/WhatsApp Chat - Familie.zip" \
  "$OUT/fake-data/phone/WhatsApp Chat with Ben Example.txt" --me "Philip Muster" \
  --out "$OUT/phone-export"

echo ""
echo "Demo exports ready:"
echo "  From the Mac app:      open $OUT/desktop-export/AnnaMuster/index.html"
echo "  From phone exports:    open $OUT/phone-export/Familie/index.html"
echo "  Machine-readable:      messages.jsonl and manifest.json in each folder"
