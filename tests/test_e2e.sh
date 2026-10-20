#!/usr/bin/env bash
#
# End-to-end test: runs the real script and the real converter on fake
# WhatsApp data (tests/make_fake_whatsapp.py): a desktop export, an update
# with a newer message, and an import of phone exports merged into it.
# Uses uv if installed, else python3 (3.9+). No network needed without uv.
#
# Run: tests/test_e2e.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPT="$REPO_ROOT/plugins/chat-export-for-whatsapp/skills/whatsapp-export/scripts/whatsapp-export.sh"

TMP="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
unset WHATSAPP_EXPORT_DIR WHATSAPP_EXPORT_PYTHON

echo "Building fake WhatsApp data"
python3 "$REPO_ROOT/tests/make_fake_whatsapp.py" "$TMP/data" >/dev/null

echo "Exporting"
bash "$SCRIPT" export --source "$TMP/data/container" --chats "Anna Muster" --out "$TMP/first" --tz Europe/Zurich
echo "Updating after a new message arrived"
python3 "$REPO_ROOT/tests/make_fake_whatsapp.py" --later "$TMP/data" >/dev/null
bash "$SCRIPT" update --source "$TMP/data/container" --from "$TMP/first" --out "$TMP/second" --tz Europe/Zurich
echo "Importing phone exports into the update"
bash "$SCRIPT" import "$TMP/data/phone/WhatsApp Chat - Familie.zip" \
  "$TMP/data/phone/WhatsApp Chat with Ben Example.txt" --me "Philip Muster" \
  --from "$TMP/second" --out "$TMP/third" --tz Europe/Zurich

echo "Checking results"
python3 - "$TMP/first" "$TMP/second" "$TMP/third" <<'EOF'
import hashlib, json, sys
from pathlib import Path

def check(out: Path, chats: int, messages: int, files: int) -> None:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["chats"] == chats, manifest["counts"]
    assert manifest["counts"]["messages"] == messages, manifest["counts"]
    assert manifest["counts"]["attachments_copied"] == files, manifest["counts"]
    for name, info in manifest["files"].items():
        data = (out / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == info["sha256"], f"checksum mismatch: {name}"
        assert len(data.decode("utf-8").splitlines()) == info["records"], name
    people = {json.loads(l)["person_id"] for l in (out / "people.jsonl").read_text(encoding="utf-8").splitlines()}
    chat_ids = set()
    for line in (out / "chats.jsonl").read_text(encoding="utf-8").splitlines():
        chat = json.loads(line)
        chat_ids.add(chat["chat_id"])
        folder = out / chat["folder"]
        assert (folder / "index.html").is_file(), f"no HTML for chat folder {folder}"
        assert (folder / "chat.md").is_file(), f"no Markdown for chat folder {folder}"
        assert set(chat["member_ids"]) <= people, "member without person record"
    ids = set()
    for line in (out / "messages.jsonl").read_text(encoding="utf-8").splitlines():
        msg = json.loads(line)
        assert msg["message_id"] not in ids, "duplicate message_id"
        ids.add(msg["message_id"])
        assert msg["chat_id"] in chat_ids, "message without chat"
        assert msg["sender_id"] is None or msg["sender_id"] in people, "sender without person record"
        assert msg["sent_at"].endswith("Z")
        for att in msg["attachments"]:
            if att["path"]:
                assert (out / att["path"]).is_file(), f"attachment missing: {att}"
    print(f"  ok    {out.name}: {chats} chats, {messages} messages, {files} files, checksums verified")

check(Path(sys.argv[1]), chats=1, messages=6, files=1)
check(Path(sys.argv[2]), chats=2, messages=11, files=2)
check(Path(sys.argv[3]), chats=4, messages=22, files=4)
EOF

echo "End-to-end test passed"
