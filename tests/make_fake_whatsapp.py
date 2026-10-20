#!/usr/bin/env python3
"""Build fake WhatsApp data with invented people and messages, for tests and the demo.

    make_fake_whatsapp.py DIR

writes:

    DIR/container/ChatStorage.sqlite   like WhatsApp for macOS (the tables and
    DIR/container/ContactsV2.sqlite    columns this tool reads), with media
    DIR/container/Message/Media/...    files in the same layout
    DIR/phone/WhatsApp Chat - Familie.zip   an iPhone export in German, with media
    DIR/phone/WhatsApp Chat with Ben Example.txt   an Android export in US English
    DIR/phone/IMG-20260105-WA0001.jpg              its attached photo

Pass --later to add one more desktop message (to test updates).
No real data is involved; every name and number is made up.
"""

from __future__ import annotations

import sqlite3
import struct
import sys
import zipfile
import zlib
from pathlib import Path

APPLE_EPOCH = 978307200


def make_png(width: int = 240, height: int = 160) -> bytes:
    """A small sunset-coloured gradient PNG, built with the standard library."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b""
    for y in range(height):
        t = y / (height - 1)
        r, g, b = int(250 - 60 * t), int(180 - 120 * t), int(90 + 60 * t)
        rows += b"\x00" + bytes((r, g, b)) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


PNG = make_png()
OPUS = b"OggS fake voice note"  # a few bytes standing in for a voice note

ANNA = "4915112345678@s.whatsapp.net"
BEN = "4917698765432@s.whatsapp.net"
CARLA_LID = "123456789012345@lid"
GROUP = "120363000000000000@g.us"


def apple(unix: int) -> float:
    return float(unix - APPLE_EPOCH)


def quote_meta(stanza: str) -> bytes:
    raw = stanza.encode("ascii")
    return bytes([0x2A, len(raw)]) + raw + b"\x32\x00"


def build_desktop(root: Path, later: bool) -> None:
    container = root / "container"
    media = container / "Message" / "Media" / ANNA / "a" / "b"
    media.mkdir(parents=True, exist_ok=True)
    (media / "photo-1.jpg").write_bytes(PNG)
    gmedia = container / "Message" / "Media" / GROUP / "c" / "d"
    gmedia.mkdir(parents=True, exist_ok=True)
    (gmedia / "voice-1.opus").write_bytes(OPUS)

    db = container / "ChatStorage.sqlite"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT,
            ZGROUPINFO INTEGER, ZSESSIONTYPE INTEGER, ZLASTMESSAGEDATE REAL);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMEMBERJID TEXT,
            ZCONTACTNAME TEXT, ZFIRSTNAME TEXT);
        CREATE TABLE ZWAPROFILEPUSHNAME (Z_PK INTEGER PRIMARY KEY, ZJID TEXT, ZPUSHNAME TEXT);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZISFROMME INTEGER,
            ZMESSAGEDATE REAL, ZSENTDATE REAL, ZTEXT TEXT, ZMESSAGETYPE INTEGER, ZGROUPMEMBER INTEGER,
            ZSTANZAID TEXT, ZFROMJID TEXT, ZTOJID TEXT, ZSTARRED INTEGER, ZMEDIAITEM INTEGER);
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER, ZMEDIALOCALPATH TEXT,
            ZTITLE TEXT, ZFILESIZE INTEGER, ZVCARDSTRING TEXT, ZVCARDNAME TEXT, ZLATITUDE REAL,
            ZLONGITUDE REAL, ZMETADATA BLOB, ZMEDIAURL TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO ZWACHATSESSION VALUES (?,?,?,?,?,?)",
        [
            (1, ANNA, "Anna Muster", None, 0, None),
            (2, GROUP, "Wandergruppe", 7, 1, None),
            (3, "status@broadcast", None, None, 3, None),
            (4, BEN, "Ben Example", None, 0, None),  # no messages: left out
        ],
    )
    conn.executemany(
        "INSERT INTO ZWAGROUPMEMBER VALUES (?,?,?,?,?)",
        [(1, 2, ANNA, None, "Anna"), (2, 2, CARLA_LID, None, None)],
    )
    conn.executemany("INSERT INTO ZWAPROFILEPUSHNAME VALUES (?,?,?)",
                     [(1, CARLA_LID, "Carla"), (2, ANNA, "Anni")])
    t = 1767225600  # 2026-01-01T00:00:00Z
    msgs = [
        # pk, chat, from_me, unix, text, type, member, stanza, starred
        (1, 1, 0, t + 60, "Hi! Are we still on for Friday?", 0, None, "3EB0AAAAAAAAAAAAAAAA01", 0),
        (2, 1, 1, t + 120, "Yes, 7pm at the usual place 🍕", 0, None, "3EB0AAAAAAAAAAAAAAAA02", 1),
        (3, 1, 0, t + 180, "Look what I found", 1, None, "3EB0AAAAAAAAAAAAAAAA03", 0),
        (4, 1, 1, t + 240, "Haha, perfect", 0, None, "3EB0AAAAAAAAAAAAAAAA04", 0),
        (5, 1, 0, t + 300, None, 5, None, "3EB0AAAAAAAAAAAAAAAA05", 0),
        (6, 1, 0, t + 360, None, 14, None, "3EB0AAAAAAAAAAAAAAAA06", 0),
        (7, 2, 0, t + 3600, "Carla added you", 6, None, None, 0),
        (8, 2, 0, t + 3660, "Who's in for Saturday?", 0, 2, "3EB0BBBBBBBBBBBBBBBB01", 0),
        (9, 2, 0, t + 3720, None, 3, 1, "3EB0BBBBBBBBBBBBBBBB02", 0),
        (10, 2, 1, t + 3780, "Count me in! https://example.org/route", 7, None, "3EB0BBBBBBBBBBBBBBBB03", 0),
        (11, 3, 0, t + 3800, "a status update", 0, None, "3EB0CCCCCCCCCCCCCCCC01", 0),
    ]
    if later:
        msgs.append((12, 1, 0, t + 86400, "See you tomorrow!", 0, None, "3EB0AAAAAAAAAAAAAAAA07", 0))
    for pk, chat, me, unix, text, kind, member, stanza, starred in msgs:
        conn.execute(
            "INSERT INTO ZWAMESSAGE VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pk, chat, me, apple(unix), apple(unix), text, kind, member, stanza, None, None, starred, None),
        )
    conn.executemany(
        "INSERT INTO ZWAMEDIAITEM VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, 3, f"Media/{ANNA}/a/b/photo-1.jpg", "Sunset", len(PNG), None, None, None, None, None, None),
            (2, 4, None, None, None, None, None, None, None, quote_meta("3EB0AAAAAAAAAAAAAAAA03"), None),
            (3, 5, None, None, None, None, None, 47.3769, 8.5417, None, None),
            (4, 9, f"Media/{GROUP}/c/d/voice-1.opus", None, len(OPUS), "audio/ogg", None, None, None, None, None),
            (5, 10, None, None, None, None, None, None, None, quote_meta("3EB0BBBBBBBBBBBBBBBB01"), None),
        ],
    )
    conn.commit()
    conn.close()

    contacts = container / "ContactsV2.sqlite"
    if not contacts.exists():
        c = sqlite3.connect(contacts)
        c.execute("CREATE TABLE ZWAADDRESSBOOKCONTACT (Z_PK INTEGER PRIMARY KEY, ZWHATSAPPID TEXT, ZFULLNAME TEXT, ZPHONENUMBER TEXT, ZLID TEXT)")
        c.execute("INSERT INTO ZWAADDRESSBOOKCONTACT VALUES (1, '4915112345678', 'Anna Muster', '+49 151 12345678', NULL)")
        c.execute("INSERT INTO ZWAADDRESSBOOKCONTACT VALUES (2, '41790000000', 'Carla Beispiel', '+41 79 000 00 00', '123456789012345')")
        c.commit()
        c.close()


IOS_CHAT = (
    "‎[05.01.26, 09:15:02] Familie: ‎Nachrichten und Anrufe sind Ende-zu-Ende-verschlüsselt.\n"
    "[05.01.26, 09:16:10] Mama: Guten Morgen ihr Lieben\n"
    "Wie geht's euch?\n"
    "[05.01.26, 09:17:45] Philip Muster: Gut, danke! ‎<Anhang: 00000003-PHOTO-2026-01-05-09-17-45.jpg>\n"
    "[05.01.26, 09:18:00] Papa: ‎Bild weggelassen\n"
    "[13.01.26, 21:03:11] Mama: Diese Nachricht wurde gelöscht.\n"
    "[13.01.26, 21:04:00] Papa: Gute Nacht 🌙\n"
)
ANDROID_CHAT = (
    "1/5/26, 6:30 PM - Messages and calls are end-to-end encrypted.\n"
    "1/5/26, 6:31 PM - Ben Example: Did you get the tickets?\n"
    "1/5/26, 6:32 PM - Philip Muster: IMG-20260105-WA0001.jpg (file attached)\n"
    "Here they are\n"
    "1/5/26, 6:33 PM - Ben Example: <Media omitted>\n"
    "1/13/26, 11:59 PM - Ben Example: Thanks!! See you there\n"
)


def build_phone(root: Path) -> None:
    phone = root / "phone"
    phone.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(phone / "WhatsApp Chat - Familie.zip", "w") as zf:
        zf.writestr("_chat.txt", IOS_CHAT)
        zf.writestr("00000003-PHOTO-2026-01-05-09-17-45.jpg", PNG)
    (phone / "WhatsApp Chat with Ben Example.txt").write_text(ANDROID_CHAT, encoding="utf-8")
    (phone / "IMG-20260105-WA0001.jpg").write_bytes(PNG)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    root = Path(args[0])
    build_desktop(root, "--later" in sys.argv)
    build_phone(root)
    print(root)


if __name__ == "__main__":
    main()
