"""Unit tests for whatsapp_export.py. Standard library only, no real WhatsApp data.

Run: python3 -m unittest discover -s tests -p "test_*.py"
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "plugins/chat-export-for-whatsapp/skills/whatsapp-export/scripts/whatsapp_export.py"
spec = importlib.util.spec_from_file_location("whatsapp_export", SCRIPT)
wa = importlib.util.module_from_spec(spec)
sys.modules["whatsapp_export"] = wa  # dataclasses look their module up here
spec.loader.exec_module(wa)

fake_spec = importlib.util.spec_from_file_location("make_fake_whatsapp", HERE / "make_fake_whatsapp.py")
fake = importlib.util.module_from_spec(fake_spec)
fake_spec.loader.exec_module(fake)

ZURICH = ZoneInfo("Europe/Zurich")


def run(*args: str) -> tuple[int, str]:
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout_null():
        code = wa.main(list(args))
    return code, err.getvalue()


class redirect_stdout_null:
    def __enter__(self):
        self._old = sys.stdout
        sys.stdout = io.StringIO()

    def __exit__(self, *exc):
        sys.stdout = self._old


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class Helpers(unittest.TestCase):
    def test_apple_time(self):
        self.assertEqual(wa.iso(wa.apple_time(0)), "2001-01-01T00:00:00.000Z")
        self.assertIsNone(wa.apple_time(None))

    def test_phone_from_jid(self):
        self.assertEqual(wa.phone_from_jid("4915112345678@s.whatsapp.net"), "+4915112345678")
        self.assertIsNone(wa.phone_from_jid("123@lid"))
        self.assertIsNone(wa.phone_from_jid("1203@g.us"))

    def test_safe_names(self):
        self.assertEqual(wa.safe_folder("Anna Müller 🎉"), "AnnaMüller")
        self.assertEqual(wa.safe_folder("🎉"), "chat")
        self.assertEqual(wa.safe_file_name("../../etc/passwd"), "passwd")
        self.assertEqual(wa.safe_file_name(".hidden"), "hidden")

    def test_until_date_is_inclusive(self):
        until = wa.parse_bound("2026-01-31", True, ZURICH)
        self.assertTrue(wa.in_range(datetime(2026, 1, 31, 23, 59, tzinfo=ZURICH), None, until))
        self.assertFalse(wa.in_range(datetime(2026, 2, 1, 0, 0, tzinfo=ZURICH), None, until))
        with self.assertRaises(wa.UserError):
            wa.parse_bound("31.01.2026", False, ZURICH)

    def test_quoted_key(self):
        self.assertEqual(wa.quoted_key(fake.quote_meta("3EB0ABCDEF0123456789")), "3EB0ABCDEF0123456789")
        self.assertIsNone(wa.quoted_key(b"\x0a\x03abc"))
        self.assertIsNone(wa.quoted_key(None))

    def test_unknown_time_zone(self):
        with self.assertRaises(wa.UserError):
            wa.local_zone("Mars/Olympus")


class PhoneFormat(unittest.TestCase):
    def test_header_formats(self):
        lines = wa.split_lines(
            "[05.01.26, 09:15:02] Anna: one\n"
            "1/5/26, 6:30 PM - Ben: two\n"
            "2026-01-05 18:31 - Carla: three\n"
            "continued\n"
            "[1/5/26, 6:30:12 PM] Dan: four\n"
        )
        self.assertEqual([l.rest for l in lines], ["Anna: one", "Ben: two", "Carla: three\ncontinued", "Dan: four"])

    def test_date_order(self):
        self.assertEqual(wa.date_order(["13.01.26", "05.01.26"], None), ("dmy", False))
        self.assertEqual(wa.date_order(["1/13/26"], None), ("mdy", False))
        self.assertEqual(wa.date_order(["2026-01-05"], None), ("ymd", False))
        self.assertEqual(wa.date_order(["01.02.26"], None), ("dmy", True))
        self.assertEqual(wa.date_order(["01.02.26"], "mdy"), ("mdy", False))

    def test_parse_stamp(self):
        dt = wa.parse_stamp("1/5/26", "6:30 PM", "mdy", ZURICH)
        self.assertEqual(dt, datetime(2026, 1, 5, 18, 30, tzinfo=ZURICH))
        self.assertEqual(wa.parse_stamp("1/5/26", "12:05 AM", "mdy", ZURICH).hour, 0)
        self.assertEqual(wa.parse_stamp("05.01.2026", "09:15:02", "dmy", ZURICH).second, 2)
        self.assertIsNone(wa.parse_stamp("31.02.26", "09:15", "dmy", ZURICH))

    def test_split_sender(self):
        self.assertEqual(wa.split_sender("Anna: hi: there"), ("Anna", "hi: there"))
        self.assertEqual(wa.split_sender("Anna joined using this group's invite link"), (None, "Anna joined using this group's invite link"))

    def test_classify(self):
        export = wa.ExportFile("x", "", {"IMG-1.jpg": Path("/nonexistent/IMG-1.jpg")})
        kind, text, atts, deleted = wa.classify("Diese Nachricht wurde gelöscht.", export)
        self.assertEqual((kind, deleted), ("deleted", True))
        kind, text, atts, _ = wa.classify("‎<attached: 00000003-PHOTO-2026.jpg> caption", export)
        self.assertEqual((kind, text, atts[0].file_name, atts[0].missing), ("image", "caption", "00000003-PHOTO-2026.jpg", True))
        kind, _, atts, _ = wa.classify("‎image omitted", export)
        self.assertEqual((kind, atts[0].missing), ("image", True))
        kind, _, _, _ = wa.classify("<Media omitted>", export)
        self.assertEqual(kind, "media")
        kind, text, atts, _ = wa.classify("notes.txt (see above)", export)
        self.assertEqual((kind, atts), ("text", []))
        kind, _, atts, _ = wa.classify("VID-1.mp4 (file attached)", export)
        self.assertEqual((kind, atts[0].missing), ("video", True))

    def test_chat_name_from_label(self):
        self.assertEqual(wa.chat_name_from_label("WhatsApp Chat with Ben Example"), "Ben Example")
        self.assertEqual(wa.chat_name_from_label("WhatsApp Chat - Familie"), "Familie")
        self.assertEqual(wa.chat_name_from_label("WhatsApp-Chat mit Anna (2)"), "Anna")
        self.assertEqual(wa.chat_name_from_label("_chat"), "_chat")


class FakeData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        fake.build_desktop(self.root, later=False)
        fake.build_phone(self.root)
        self.container = self.root / "container"

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, *extra: str) -> Path:
        out = self.root / f"out-{len(list(self.root.glob('out-*')))}"
        code, err = run("export", "--source", str(self.container), "--out", str(out), "--tz", "Europe/Zurich", *extra)
        self.assertEqual(code, 0, err)
        return out

    def test_desktop_export(self):
        out = self.export()
        msgs = read_jsonl(out / "messages.jsonl")
        chats = {c["name"]: c for c in read_jsonl(out / "chats.jsonl")}
        people = {p["person_id"]: p for p in read_jsonl(out / "people.jsonl")}
        self.assertEqual(set(chats), {"Anna Muster", "Wandergruppe"}, "status and empty chats are left out")
        self.assertEqual(chats["Wandergruppe"]["type"], "group")
        self.assertEqual(len(msgs), 10)
        carla = people[fake.CARLA_LID]
        self.assertEqual((carla["name"], carla["phone"]), ("Carla Beispiel", "+41790000000"), "LID resolved via contacts")
        by_text = {m["text"]: m for m in msgs}
        reply = by_text["Haha, perfect"]["reply_to"]
        self.assertTrue(reply["found"])
        self.assertEqual(reply["text"], "Sunset")
        self.assertTrue(by_text["Yes, 7pm at the usual place 🍕"]["starred"])
        photo = by_text["Sunset"]["attachments"][0]
        self.assertEqual(photo["content_type"], "image/jpeg")
        self.assertTrue((out / photo["path"]).is_file())
        loc = next(m for m in msgs if m["type"] == "location")
        self.assertEqual(loc["location"], {"latitude": 47.3769, "longitude": 8.5417})
        self.assertTrue(next(m for m in msgs if m["type"] == "deleted")["deleted"])
        system = next(m for m in msgs if m["type"] == "system")
        self.assertIsNone(system["sender_id"])
        self.assertIsNone(system["direction"])
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "1.0")
        self.assertEqual(manifest["source"]["kind"], "desktop")
        self.assertEqual(manifest["timezone"], "Europe/Zurich")

    def test_original_database_is_untouched(self):
        db = self.container / "ChatStorage.sqlite"
        before = db.read_bytes()
        self.export()
        self.assertEqual(db.read_bytes(), before)

    def test_filters(self):
        out = self.export("--chats", "anna muster", "--until", "2026-01-01", "--no-attachments")
        msgs = read_jsonl(out / "messages.jsonl")
        self.assertEqual({m["chat_id"] for m in msgs}, {"wa:" + fake.ANNA})
        self.assertTrue(all(a["path"] is None for m in msgs for a in m["attachments"]))
        self.assertFalse((out / "AnnaMuster" / "media").exists())
        code, err = run("export", "--source", str(self.container), "--out", str(self.root / "x"), "--chats", "Nobody")
        self.assertEqual(code, 1)
        self.assertIn("No chat named: Nobody", err)

    def test_names_without_invisible_marks_and_with_commas(self):
        import sqlite3
        conn = sqlite3.connect(self.container / "ChatStorage.sqlite")
        conn.execute("UPDATE ZWACHATSESSION SET ZPARTNERNAME = '\u202aTrip, 08.07. \u202c' WHERE Z_PK = 2")
        conn.commit()
        conn.close()
        out = self.export("--chats", "Trip, 08.07.,Anna Muster")
        names = sorted(c["name"] for c in read_jsonl(out / "chats.jsonl"))
        self.assertEqual(names, ["Anna Muster", "Trip, 08.07."])

    def test_no_data(self):
        out = self.export("--no-data")
        self.assertFalse((out / "messages.jsonl").exists())
        self.assertTrue((out / "AnnaMuster" / "index.html").is_file())

    def test_update_keeps_messages_the_app_dropped(self):
        first = self.export("--chats", "Anna Muster")
        fake.build_desktop(self.root, later=True)
        # Simulate the app no longer holding an old message.
        import sqlite3
        conn = sqlite3.connect(self.container / "ChatStorage.sqlite")
        conn.execute("DELETE FROM ZWAMESSAGE WHERE Z_PK = 1")
        conn.commit()
        conn.close()
        second = self.root / "second"
        code, err = run("export", "--source", str(self.container), "--chats", "Anna Muster",
                        "--merge-from", str(first), "--out", str(second))
        self.assertEqual(code, 0, err)
        texts = [m["text"] for m in read_jsonl(second / "messages.jsonl")]
        self.assertIn("Hi! Are we still on for Friday?", texts, "kept from the earlier export")
        self.assertIn("See you tomorrow!", texts, "new message added")
        ids = [m["message_id"] for m in read_jsonl(second / "messages.jsonl")]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 7)
        photo = next(m for m in read_jsonl(second / "messages.jsonl") if m["text"] == "Sunset")["attachments"][0]
        self.assertTrue((second / photo["path"]).is_file())
        self.assertIn("Kept 1 messages", err)

    def test_merge_refuses_foreign_folder(self):
        code, err = run("export", "--source", str(self.container), "--merge-from", str(self.root),
                        "--out", str(self.root / "y"))
        self.assertEqual(code, 1)
        self.assertIn("not an export made by this tool", err)

    def test_existing_out_is_refused(self):
        code, err = run("export", "--source", str(self.container), "--out", str(self.root))
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_schema_problem_is_explained(self):
        import sqlite3
        conn = sqlite3.connect(self.container / "ChatStorage.sqlite")
        conn.execute("ALTER TABLE ZWAMESSAGE RENAME COLUMN ZTEXT TO ZBODY")
        conn.commit()
        conn.close()
        code, err = run("export", "--source", str(self.container), "--out", str(self.root / "z"))
        self.assertEqual(code, 1)
        self.assertIn("ZWAMESSAGE.ZTEXT is missing", err)
        self.assertIn("inspect", err)

    def test_inspect_shows_no_content(self):
        text = wa.inspect_database(self.container / "ChatStorage.sqlite")
        self.assertIn("ZWAMESSAGE (11 rows)", text)
        self.assertNotIn("Friday", text)

    def test_phone_import(self):
        out = self.root / "phone-out"
        code, err = run("import", str(self.root / "phone" / "WhatsApp Chat - Familie.zip"),
                        str(self.root / "phone" / "WhatsApp Chat with Ben Example.txt"),
                        "--me", "Philip Muster", "--tz", "Europe/Zurich", "--out", str(out))
        self.assertEqual(code, 0, err)
        msgs = read_jsonl(out / "messages.jsonl")
        chats = {c["name"]: c for c in read_jsonl(out / "chats.jsonl")}
        self.assertEqual(chats["Familie"]["type"], "group")
        self.assertEqual(chats["Ben Example"]["type"], "direct")
        self.assertNotIn("name:Familie", chats["Familie"]["member_ids"], "group notices aren't a person")
        fam = [m for m in msgs if m["chat_id"] == chats["Familie"]["chat_id"]]
        self.assertEqual(fam[0]["type"], "system")
        self.assertEqual(fam[1]["text"], "Guten Morgen ihr Lieben\nWie geht's euch?")
        self.assertEqual(fam[1]["sent_at"], "2026-01-05T08:16:10.000Z", "local time converted to UTC")
        mine = [m for m in msgs if m["direction"] == "outgoing"]
        self.assertEqual(len(mine), 2)
        self.assertTrue(all(m["sender_id"] == "me" for m in mine))
        ben = [m for m in msgs if m["chat_id"] == chats["Ben Example"]["chat_id"]]
        self.assertEqual(ben[-1]["sent_at"], "2026-01-13T22:59:00.000Z", "12-hour US time read correctly")
        for m in msgs:
            for a in m["attachments"]:
                if a["path"]:
                    self.assertTrue((out / a["path"]).is_file())

    def test_phone_import_without_me_lists_senders(self):
        out = self.root / "phone-out2"
        code, err = run("import", str(self.root / "phone" / "WhatsApp Chat with Ben Example.txt"), "--out", str(out))
        self.assertEqual(code, 0, err)
        self.assertIn("Pass --me", err)
        self.assertIn("Philip Muster (1)", err)
        self.assertTrue(all(m["direction"] is None for m in read_jsonl(out / "messages.jsonl")))

    def test_import_ids_are_stable(self):
        paths = [str(self.root / "phone" / "WhatsApp Chat - Familie.zip")]
        a, b = self.root / "ia", self.root / "ib"
        self.assertEqual(run("import", *paths, "--out", str(a), "--tz", "Europe/Zurich")[0], 0)
        self.assertEqual(run("import", *paths, "--out", str(b), "--tz", "Europe/Zurich")[0], 0)
        ids_a = [m["message_id"] for m in read_jsonl(a / "messages.jsonl")]
        ids_b = [m["message_id"] for m in read_jsonl(b / "messages.jsonl")]
        self.assertEqual(ids_a, ids_b)

    def test_hostile_zip_names_stay_inside_the_export(self):
        evil = self.root / "phone" / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("_chat.txt", "[05.01.26, 09:15:02] Eve: <attached: ../../escape.jpg>\n"
                                     "[05.01.26, 09:16:02] Eve: <script>alert(1)</script>\n")
            zf.writestr("../../escape.jpg", fake.PNG)
        out = self.root / "evil-out"
        code, err = run("import", str(evil), "--out", str(out))
        self.assertEqual(code, 0, err)
        self.assertFalse((self.root / "escape.jpg").exists())
        files = [p for p in out.rglob("*") if p.is_file()]
        self.assertTrue(all(out in p.parents for p in files))
        page = (out / "evil" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_not_a_chat_export(self):
        bad = self.root / "notes.txt"
        bad.write_text("just some notes\n", encoding="utf-8")
        code, err = run("import", str(bad), "--out", str(self.root / "bad-out"))
        self.assertEqual(code, 1)
        self.assertIn("doesn't look like a WhatsApp chat export", err)


class CommandLine(unittest.TestCase):
    def test_version_flag(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--version"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn(wa.VERSION, result.stdout)

    def test_versions_match(self):
        root = HERE.parent
        plugin = json.loads((root / "plugins/chat-export-for-whatsapp/.claude-plugin/plugin.json").read_text())
        shell = (SCRIPT.parent / "whatsapp-export.sh").read_text()
        self.assertEqual(plugin["version"], wa.VERSION)
        self.assertIn(f'readonly VERSION="{wa.VERSION}"', shell)
        self.assertEqual(wa.GENERATOR["version"], wa.VERSION)


if __name__ == "__main__":
    unittest.main()
