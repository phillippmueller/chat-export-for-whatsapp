#!/usr/bin/env python3
"""Export WhatsApp chats to HTML, Markdown and machine-readable JSON Lines.

Two sources are supported:

  desktop  The WhatsApp app for macOS keeps its chats in a local SQLite
           database (ChatStorage.sqlite in its group container). This script
           reads a consistent snapshot of it, read-only, and never changes it.
  import   Files made with WhatsApp's own "Export chat" on a phone (.zip with
           media, or .txt without), on any operating system.

Both write the same export layout: per chat an index.html, a chat.md and a
media folder, plus people.jsonl, chats.jsonl, messages.jsonl and
manifest.json. The schema is documented in reference/data-format.md.

Standard library only (Python 3.9 or newer). Normally started by
whatsapp-export.sh; run with --help for the commands.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import quote

VERSION = "0.1.0"
SCHEMA_VERSION = "1.0"
GENERATOR = {
    "name": "chat-export-for-whatsapp",
    "version": VERSION,
    "url": "https://github.com/phillippmueller/chat-export-for-whatsapp",
}

# Seconds between the Unix epoch and Apple's reference date (2001-01-01 UTC),
# which WhatsApp's Apple apps use for every timestamp.
APPLE_EPOCH = 978307200
CONTAINER = Path.home() / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
ME = "me"

JsonDict = dict[str, Any]


class UserError(Exception):
    """An error with a message meant for the person running the script."""


# =============================================================== helpers ===


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_ms(dt: Optional[datetime]) -> Optional[int]:
    return None if dt is None else int(round(dt.timestamp() * 1000))


def apple_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(APPLE_EPOCH + float(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def local_zone(name: Optional[str] = None) -> tuple[tzinfo, str]:
    """The time zone to read phone exports in and to show times in.

    Uses NAME if given, else $TZ, else the system zone from /etc/localtime.
    Falls back to the current fixed UTC offset if no zone name can be found.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    candidates = [name] if name else [os.environ.get("TZ", "").lstrip(":") or None]
    if not name:
        try:
            target = os.path.realpath("/etc/localtime")
            if "zoneinfo/" in target:
                candidates.append(target.split("zoneinfo/", 1)[1])
        except OSError:
            pass
    for key in candidates:
        if not key:
            continue
        try:
            return ZoneInfo(key), key
        except (ZoneInfoNotFoundError, ValueError):
            if name:
                raise UserError(f"Unknown time zone: {name} (use a name like Europe/Zurich)")
    offset = datetime.now().astimezone().tzinfo or timezone.utc
    return offset, str(offset)


def parse_bound(text: Optional[str], end: bool, zone: tzinfo) -> Optional[datetime]:
    """Parse --since/--until. A plain date means the whole day (local time)."""
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            day = date.fromisoformat(text)
            if end:
                day += timedelta(days=1)
            return datetime(day.year, day.month, day.day, tzinfo=zone)
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise UserError(f"Not a valid date: {text} (use e.g. 2026-01-31 or 2026-01-31T09:00:00+01:00)")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    return dt


def in_range(dt: Optional[datetime], since: Optional[datetime], until: Optional[datetime]) -> bool:
    if dt is None:
        return since is None and until is None
    if since and dt < since:
        return False
    if until and dt >= until:
        return False
    return True


def phone_from_jid(jid: Optional[str]) -> Optional[str]:
    if jid and jid.endswith("@s.whatsapp.net"):
        digits = jid.split("@", 1)[0].split(":", 1)[0]
        if digits.isdigit():
            return "+" + digits
    return None


# Direction marks WhatsApp puts around names and numbers ("\u202a+41 79 …\u202c").
# They are invisible, so nobody can type them in --chats; names drop them.
BIDI_MARKS = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff"), None)


def clean_name(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return value.translate(BIDI_MARKS).strip() or None


def short_hash(*parts: Any, length: int = 20) -> str:
    h = hashlib.sha256("\x1f".join("" if p is None else str(p) for p in parts).encode("utf-8"))
    return h.hexdigest()[:length]


def safe_folder(name: str) -> str:
    """A folder name that is safe on every file system."""
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "-_")
    return cleaned[:60] or "chat"


def safe_file_name(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/")).strip()
    base = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", base).lstrip(".")
    return base[:120] or "file"


def guess_type(file_name: Optional[str]) -> Optional[str]:
    if not file_name:
        return None
    return mimetypes.guess_type(file_name)[0]


# ================================================================ model ===


@dataclass
class Attachment:
    file_name: Optional[str]
    content_type: Optional[str]
    size_bytes: Optional[int]
    # Where to copy the file from: a path, or a zip member (zip path, name).
    source: Any = None
    path: Optional[str] = None  # relative to the export folder, once copied
    missing: bool = False

    def record(self) -> JsonDict:
        return {
            "file_name": self.file_name,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "path": self.path,
            "missing": self.missing,
        }


@dataclass
class Dataset:
    source: str  # "desktop" or "phone-export"
    people: dict[str, JsonDict] = field(default_factory=dict)
    chats: dict[str, JsonDict] = field(default_factory=dict)
    # Messages carry an extra key "_attachments" (list of Attachment) until written.
    messages: list[JsonDict] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)

    def add_person(self, person: JsonDict) -> str:
        pid = person["person_id"]
        known = self.people.get(pid)
        if known is None:
            self.people[pid] = person
        else:
            for key, value in person.items():
                if known.get(key) in (None, "") and value not in (None, ""):
                    known[key] = value
        return pid


def message_record(**fields: Any) -> JsonDict:
    """A message with every schema field present, in a fixed order."""
    record: JsonDict = {
        "message_id": None,
        "wa_key": None,
        "chat_id": None,
        "sender_id": None,
        "sender_name": None,
        "direction": None,
        "type": "text",
        "wa_type": None,
        "sent_at": None,
        "sent_at_ms": None,
        "text": "",
        "reply_to": None,
        "reactions": [],
        "attachments": [],
        "location": None,
        "deleted": False,
        "starred": False,
        "source": None,
    }
    record.update(fields)
    return record


# ======================================================= desktop source ===

# WhatsApp's message type codes, as far as they are known. Anything else is
# exported with type "other" and its raw code in wa_type.
DESKTOP_TYPES = {
    0: "text",
    1: "image",
    2: "video",
    3: "audio",
    4: "contact",
    5: "location",
    6: "system",
    7: "link",
    8: "document",
    11: "gif",
    14: "deleted",
    15: "sticker",
}
NEEDED = {
    "ZWACHATSESSION": ["Z_PK", "ZCONTACTJID", "ZPARTNERNAME"],
    "ZWAMESSAGE": ["Z_PK", "ZCHATSESSION", "ZISFROMME", "ZMESSAGEDATE", "ZTEXT", "ZMESSAGETYPE"],
}
HIDDEN_JIDS = {"status@broadcast"}


def open_snapshot(db_path: Path, workdir: Path) -> sqlite3.Connection:
    """Open a private, consistent copy of the database; never the original.

    Tries SQLite's backup API on a read-only connection first, which is safe
    while WhatsApp is running. Falls back to copying the database files.
    """
    if not db_path.is_file():
        raise UserError(f"No WhatsApp database found at: {db_path}")
    copy = workdir / "snapshot.sqlite"
    try:
        src = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(copy)
            src.backup(dst)
            dst.close()
        finally:
            src.close()
    except sqlite3.Error as exc:
        if "authoriz" in str(exc).lower():
            raise_permission(db_path)
        for suffix in ("", "-wal", "-shm"):
            part = Path(str(db_path) + suffix)
            if part.exists():
                try:
                    shutil.copyfile(part, str(copy) + suffix)
                except PermissionError:
                    raise_permission(db_path)
    except PermissionError:
        raise_permission(db_path)
    try:
        conn = sqlite3.connect(copy)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        raise UserError(f"Could not read the WhatsApp database ({exc}).")
    return conn


def raise_permission(db_path: Path) -> None:
    raise UserError(
        f"macOS blocked access to WhatsApp's data ({db_path.parent}).\n"
        "       Run the command again and click Allow when macOS asks about accessing\n"
        "       data from other apps, or give the app running this script Full Disk\n"
        "       Access in System Settings > Privacy & Security."
    )


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error:
        return set()


def check_schema(conn: sqlite3.Connection) -> None:
    problems = []
    for table, cols in NEEDED.items():
        have = columns(conn, table)
        if not have:
            problems.append(f"table {table} is missing")
        else:
            problems += [f"column {table}.{c} is missing" for c in cols if c not in have]
    if problems:
        raise UserError(
            "WhatsApp's database format is not what this tool expects: "
            + "; ".join(problems)
            + ".\n       Run the 'inspect' command and report its output (it contains no messages)."
        )


def col(have: set[str], table: str, name: str) -> str:
    """Select a column if it exists, else NULL under the same name."""
    return f"{table}.{name}" if name in have else f"NULL AS {name}"


def inspect_database(db_path: Path) -> str:
    """Tables, columns and row counts only, no content: safe to share."""
    with tempfile.TemporaryDirectory(prefix="wa-export-") as tmp:
        conn = open_snapshot(db_path, Path(tmp))
        lines = [f"Database: {db_path.name}"]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            if not table.startswith("Z"):
                continue
            count = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            lines.append(f"{table} ({count} rows): {', '.join(sorted(columns(conn, table)))}")
        conn.close()
    return "\n".join(lines)


def address_book(container: Path, workdir: Path) -> dict[str, JsonDict]:
    """Names and phone numbers from ContactsV2.sqlite, if present and readable."""
    path = container / "ContactsV2.sqlite"
    if not path.is_file():
        return {}
    try:
        sub = workdir / "contacts"
        sub.mkdir(exist_ok=True)
        conn = open_snapshot(path, sub)
    except UserError:
        return {}
    have = columns(conn, "ZWAADDRESSBOOKCONTACT")
    if "ZWHATSAPPID" not in have:
        conn.close()
        return {}
    cols = ", ".join(col(have, "ZWAADDRESSBOOKCONTACT", c) for c in ("ZWHATSAPPID", "ZFULLNAME", "ZPHONENUMBER", "ZLID"))
    result: dict[str, JsonDict] = {}
    for row in conn.execute(f"SELECT {cols} FROM ZWAADDRESSBOOKCONTACT"):
        jid = row["ZWHATSAPPID"]
        if not jid:
            continue
        if "@" not in jid:
            jid += "@s.whatsapp.net"
        entry = {"name": clean_name(row["ZFULLNAME"]), "phone": phone_from_jid(jid)}
        result[jid] = entry
        if row["ZLID"]:
            lid = row["ZLID"] if "@" in row["ZLID"] else row["ZLID"] + "@lid"
            result[lid] = entry
    conn.close()
    return result


def quoted_key(metadata: Any) -> Optional[str]:
    """The quoted message's ID from a media item's metadata (protobuf field 5)."""
    if not isinstance(metadata, (bytes, bytearray)) or len(metadata) < 3 or metadata[0] != 0x2A:
        return None
    length, pos, shift = 0, 1, 0
    while pos < len(metadata):
        byte = metadata[pos]
        length |= (byte & 0x7F) << shift
        pos += 1
        if not byte & 0x80:
            break
        shift += 7
    raw = bytes(metadata[pos : pos + length])
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    return text if re.fullmatch(r"[0-9A-Za-z_-]{6,64}", text) else None


def chat_label(row: sqlite3.Row) -> str:
    return clean_name(row["ZPARTNERNAME"]) or phone_from_jid(row["ZCONTACTJID"]) or row["ZCONTACTJID"] or "Unknown"


def desktop_chats(conn: sqlite3.Connection) -> list[JsonDict]:
    have = columns(conn, "ZWACHATSESSION")
    sql = f"""
        SELECT s.Z_PK, s.ZCONTACTJID, s.ZPARTNERNAME,
               {col(have, 's', 'ZGROUPINFO')},
               {col(have, 's', 'ZSESSIONTYPE')},
               count(m.Z_PK) AS n, max(m.ZMESSAGEDATE) AS last
        FROM ZWACHATSESSION s LEFT JOIN ZWAMESSAGE m ON m.ZCHATSESSION = s.Z_PK
        GROUP BY s.Z_PK ORDER BY last DESC
    """
    chats = []
    for row in conn.execute(sql):
        jid = row["ZCONTACTJID"] or ""
        if jid in HIDDEN_JIDS or not row["n"]:
            continue
        if row["ZGROUPINFO"] is not None or jid.endswith("@g.us"):
            kind = "group"
        elif jid.endswith("@broadcast"):
            kind = "broadcast"
        else:
            kind = "direct"
        chats.append({"pk": row["Z_PK"], "jid": jid, "name": chat_label(row), "type": kind,
                      "count": row["n"], "last": apple_time(row["last"])})
    return chats


def select_chats(chats: list[JsonDict], wanted: Optional[str]) -> list[JsonDict]:
    """Pick chats by the comma-separated names given with --chats.

    Names may contain commas themselves ("Trip, 08.07."), so the longest run of
    comma-separated parts that matches a chat name wins. Matching ignores
    invisible direction marks, surrounding spaces and, as a fallback, case.
    """
    if not wanted:
        return chats

    def find(name: str) -> list[JsonDict]:
        key = clean_name(name) or ""
        hits = [c for c in chats if clean_name(c["name"]) == key or c["jid"] == key]
        return hits or [c for c in chats if (clean_name(c["name"]) or "").casefold() == key.casefold()]

    parts = wanted.split(",")
    picked: list[JsonDict] = []
    missing: list[str] = []
    i = 0
    while i < len(parts):
        for j in range(len(parts), i, -1):
            hits = find(",".join(parts[i:j]))
            if hits:
                picked.extend(hits)
                if len(hits) > 1:
                    print(f"Note: {len(hits)} chats are called '{clean_name(','.join(parts[i:j]))}'; "
                          "exporting all of them. To pick one, use its ID from 'list --ids'.", file=sys.stderr)
                i = j
                break
        else:
            if parts[i].strip():
                missing.append(parts[i].strip())
            i += 1
    if missing:
        hints = []
        names = sorted({clean_name(c["name"]) or "" for c in chats})
        for m in missing:
            low = m.casefold()
            close = [n for n in names if n.casefold().startswith(low)][:3]
            close += [n for n in difflib.get_close_matches(m, names, n=3, cutoff=0.75) if n not in close]
            if close:
                hints.append(f"for '{m}' did you mean: " + "; ".join(close[:3]) + "?")
        raise UserError("No chat named: " + ", ".join(missing) + " (see the exact names with 'list')"
                        + "".join("\n       " + h for h in hints))
    unique = {c["pk"]: c for c in picked}
    return list(unique.values())


def read_desktop(container: Path, wanted: Optional[str], since: Optional[datetime],
                 until: Optional[datetime], workdir: Path) -> Dataset:
    conn = open_snapshot(container / "ChatStorage.sqlite", workdir)
    check_schema(conn)
    data = Dataset(source="desktop", inputs=["ChatStorage.sqlite"])
    contacts = address_book(container, workdir)

    msg_cols = columns(conn, "ZWAMESSAGE")
    media_cols = columns(conn, "ZWAMEDIAITEM")
    member_cols = columns(conn, "ZWAGROUPMEMBER")
    push_cols = columns(conn, "ZWAPROFILEPUSHNAME")

    push_names: dict[str, str] = {}
    if {"ZJID", "ZPUSHNAME"} <= push_cols:
        push_names = {r[0]: clean_name(r[1]) for r in conn.execute("SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME") if r[0] and clean_name(r[1])}

    all_chats = desktop_chats(conn)
    # Names of direct chats are the best names for those people.
    partner = {c["jid"]: c["name"] for c in all_chats if c["type"] == "direct"}

    members: dict[int, JsonDict] = {}
    if "ZMEMBERJID" in member_cols:
        cols = ", ".join(col(member_cols, "ZWAGROUPMEMBER", c) for c in ("Z_PK", "ZMEMBERJID", "ZCONTACTNAME", "ZFIRSTNAME", "ZCHATSESSION"))
        for r in conn.execute(f"SELECT {cols} FROM ZWAGROUPMEMBER"):
            members[r["Z_PK"]] = dict(r)

    def person(jid: Optional[str], hint: Optional[str] = None) -> Optional[str]:
        if not jid:
            return None
        book = contacts.get(jid, {})
        name = partner.get(jid) or book.get("name") or clean_name(hint) or push_names.get(jid)
        phone = phone_from_jid(jid) or book.get("phone")
        return data.add_person({
            "person_id": jid, "jid": jid, "name": name or phone or jid.split("@")[0],
            "push_name": push_names.get(jid), "phone": phone, "is_me": False,
        })

    data.add_person({"person_id": ME, "jid": None, "name": "Me", "push_name": None, "phone": None, "is_me": True})

    chosen = select_chats(all_chats, wanted)
    media_join = "LEFT JOIN ZWAMEDIAITEM i ON i.ZMESSAGE = m.Z_PK" if "ZMESSAGE" in media_cols else ""
    def mcol(name: str) -> str:
        return col(media_cols, "i", name) if media_join else f"NULL AS {name}"
    select = ", ".join([
        "m.Z_PK", "m.ZISFROMME", "m.ZMESSAGEDATE", "m.ZTEXT", "m.ZMESSAGETYPE",
        col(msg_cols, "m", "ZSTANZAID"), col(msg_cols, "m", "ZGROUPMEMBER"),
        col(msg_cols, "m", "ZFROMJID"), col(msg_cols, "m", "ZSTARRED"),
        mcol("ZMEDIALOCALPATH"), mcol("ZTITLE"), mcol("ZFILESIZE"), mcol("ZVCARDSTRING"),
        mcol("ZVCARDNAME"), mcol("ZLATITUDE"), mcol("ZLONGITUDE"), mcol("ZMETADATA"),
    ])

    by_key: dict[tuple[str, str], JsonDict] = {}
    pending_replies: list[tuple[JsonDict, str]] = []

    for chat in chosen:
        chat_id = "wa:" + chat["jid"]
        direct_pid = person(chat["jid"], chat["name"]) if chat["type"] == "direct" else None
        member_ids = [ME] + ([direct_pid] if direct_pid else [])
        for m in members.values():
            if m.get("ZCHATSESSION") == chat["pk"]:
                pid = person(m["ZMEMBERJID"], m.get("ZCONTACTNAME") or m.get("ZFIRSTNAME"))
                if pid and pid not in member_ids:
                    member_ids.append(pid)
        data.chats[chat_id] = {
            "chat_id": chat_id, "jid": chat["jid"], "name": chat["name"], "type": chat["type"],
            "folder": None, "person_id": direct_pid, "member_ids": member_ids,
            "message_count": 0, "first_message_at": None, "last_message_at": None,
            "source": "desktop",
        }

        sql = f"SELECT {select} FROM ZWAMESSAGE m {media_join} WHERE m.ZCHATSESSION = ? ORDER BY m.ZMESSAGEDATE, m.Z_PK"
        seen_pk: set[int] = set()
        for row in conn.execute(sql, (chat["pk"],)):
            if row["Z_PK"] in seen_pk:  # several media rows for one message
                continue
            seen_pk.add(row["Z_PK"])
            sent = apple_time(row["ZMESSAGEDATE"])
            if not in_range(sent, since, until):
                continue
            code = row["ZMESSAGETYPE"]
            kind = DESKTOP_TYPES.get(code, "other")
            from_me = bool(row["ZISFROMME"])
            if kind == "system":
                sender = None
            elif from_me:
                sender = ME
            elif chat["type"] == "group":
                member = members.get(row["ZGROUPMEMBER"]) if row["ZGROUPMEMBER"] else None
                jid = (member or {}).get("ZMEMBERJID") or row["ZFROMJID"]
                sender = person(jid, (member or {}).get("ZCONTACTNAME"))
                if sender and sender not in member_ids:
                    member_ids.append(sender)
            else:
                sender = direct_pid
            stanza = row["ZSTANZAID"]
            message_id = (f"{chat_id}:{sender or 'system'}:{stanza}" if stanza else f"{chat_id}:local:{row['Z_PK']}")
            text = row["ZTEXT"] or ""
            if kind == "system" and text.strip().isdigit():
                text = ""
            if kind in ("image", "video", "audio", "document", "gif", "sticker") and row["ZTITLE"]:
                text = row["ZTITLE"]

            attachments: list[Attachment] = []
            local = row["ZMEDIALOCALPATH"]
            if local and kind not in ("location", "contact", "system"):
                src = container / "Message" / local
                if not src.is_file():
                    src = container / local
                name = safe_file_name(local)
                vtype = row["ZVCARDSTRING"]
                ctype = vtype if (isinstance(vtype, str) and re.fullmatch(r"[\w.+-]+/[\w.+-]+", vtype)) else guess_type(name)
                size = row["ZFILESIZE"] if isinstance(row["ZFILESIZE"], int) and row["ZFILESIZE"] > 0 else None
                attachments.append(Attachment(name, ctype, size, src if src.is_file() else None, missing=not src.is_file()))
            if kind == "contact" and isinstance(row["ZVCARDSTRING"], str) and "BEGIN:VCARD" in row["ZVCARDSTRING"]:
                card_name = safe_file_name((row["ZVCARDNAME"] or "contact").split("_$!<")[0]) + ".vcf"
                attachments.append(Attachment(card_name, "text/vcard", None, ("text", row["ZVCARDSTRING"])))
                text = text or (row["ZVCARDNAME"] or "").split("_$!<")[0]
            location = None
            if kind == "location" and row["ZLATITUDE"] is not None and row["ZLONGITUDE"] is not None:
                location = {"latitude": row["ZLATITUDE"], "longitude": row["ZLONGITUDE"]}

            sender_name = data.people[sender]["name"] if sender else None
            record = message_record(
                message_id=message_id, wa_key=stanza, chat_id=chat_id, sender_id=sender,
                sender_name=sender_name,
                direction=None if kind == "system" else ("outgoing" if from_me else "incoming"),
                type=kind, wa_type=code, sent_at=iso(sent), sent_at_ms=to_ms(sent), text=text,
                location=location, deleted=kind == "deleted", starred=bool(row["ZSTARRED"]),
                source="desktop",
            )
            record["_attachments"] = attachments
            data.messages.append(record)
            if stanza:
                by_key[(chat_id, stanza)] = record
            quoted = quoted_key(row["ZMETADATA"])
            if quoted:
                pending_replies.append((record, quoted))

    # Link replies once every message is known. Older WhatsApp versions store
    # only the first 17 characters of the quoted ID, so match on that too.
    prefixes: dict[tuple[str, str], JsonDict] = {}
    for (cid, key), rec in by_key.items():
        prefixes.setdefault((cid, key[:17]), rec)
    for record, quoted in pending_replies:
        target = by_key.get((record["chat_id"], quoted)) or prefixes.get((record["chat_id"], quoted[:17]))
        record["reply_to"] = {
            "wa_key": target["wa_key"] if target else quoted,
            "message_id": target["message_id"] if target else None,
            "author_id": target["sender_id"] if target else None,
            "sent_at": target["sent_at"] if target else None,
            "text": target["text"] if target else None,
            "found": target is not None,
        }
    conn.close()
    return data


# ===================================================== phone-export source ===

HEADER_IOS = re.compile(
    r"^\[(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+(?P<time>[^\]]+)\]\s(?P<rest>.*)$"
)
HEADER_ANDROID = re.compile(
    r"^(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+"
    r"(?P<time>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s?[AaPp]\.?\s?[Mm]\.?)?)\s[-–]\s(?P<rest>.*)$"
)
TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?\s*([AaPp])?\.?\s*(?:[Mm]\.?)?$")
INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮﻿"), None)
ATTACHED_IOS = re.compile(r"<[^<>:]{2,40}:\s*(?P<file>[^<>]+?\.[A-Za-z0-9]{1,6})>")
ATTACHED_ANDROID = re.compile(r"^(?P<file>[^\s/\\]+\.[A-Za-z0-9]{1,6}) \((?P<note>[^)]{2,40})\)\s*(?P<caption>[\s\S]*)$")
ANDROID_ATTACH_NOTES = ("file attached", "datei angehängt", "fichier joint", "archivo adjunto",
                        "file allegato", "arquivo anexado", "bestand bijgevoegd")
OMITTED_WORDS = ("omitted", "weggelassen", "ausgeschlossen", "omis", "omise", "omitido", "omitida",
                 "omesso", "omessa", "weggelaten", "ocultado", "ocultada")
OMITTED_KIND = {
    "image": "image", "bild": "image", "photo": "image", "video": "video", "audio": "audio",
    "sticker": "sticker", "gif": "gif", "document": "document", "dokument": "document",
    "contact card": "contact", "kontaktkarte": "contact", "media": "media", "medien": "media",
}
DELETED_TEXTS = (
    "this message was deleted", "you deleted this message", "diese nachricht wurde gelöscht",
    "du hast diese nachricht gelöscht", "ce message a été supprimé", "vous avez supprimé ce message",
    "se eliminó este mensaje", "eliminaste este mensaje", "questo messaggio è stato eliminato",
)
CHAT_NAME_PREFIX = re.compile(
    r"^(?:WhatsApp[ -]Chat|Chat de WhatsApp|Discussion WhatsApp|Chat WhatsApp|WhatsApp-chat)"
    r"\s*(?:with|mit|con|avec|met|com|-|–)\s+(?P<name>.+)$",
    re.IGNORECASE,
)
FILE_KINDS = {
    "image": "image", "video": "video", "audio": "audio",
}


@dataclass
class ExportFile:
    """One phone export: the chat text plus a way to find its media files."""
    label: str
    text: str
    members: dict[str, Any]  # file name -> source (Path or ("zip", path, member))


def load_export(path: Path) -> ExportFile:
    if not path.exists():
        raise UserError(f"File not found: {path}")
    if path.is_dir():
        txts = sorted(path.glob("*.txt"))
        chat = next((t for t in txts if t.name == "_chat.txt"), txts[0] if len(txts) == 1 else None)
        if chat is None:
            raise UserError(f"Expected exactly one chat .txt file in {path}")
        members = {p.name: p for p in path.iterdir() if p.is_file() and p != chat}
        return ExportFile(path.name, chat.read_text(encoding="utf-8-sig", errors="replace"), members)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            txts = [n for n in names if n.lower().endswith(".txt")]
            chat_name = next((n for n in txts if os.path.basename(n) == "_chat.txt"), txts[0] if len(txts) == 1 else None)
            if chat_name is None:
                raise UserError(f"Expected exactly one chat .txt file in {path.name}")
            text = zf.read(chat_name).decode("utf-8-sig", errors="replace")
            members = {}
            for n in names:
                base = os.path.basename(n)
                if n != chat_name and base and base not in members:
                    members[base] = ("zip", path, n)
        return ExportFile(path.stem, text, members)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    members = {p.name: p for p in path.parent.iterdir() if p.is_file() and p != path}
    return ExportFile(path.stem, text, members)


def chat_name_from_label(label: str) -> str:
    label = re.sub(r"\s*\(\d+\)$", "", label.strip())
    m = CHAT_NAME_PREFIX.match(label)
    return (m.group("name") if m else label).strip() or "WhatsApp chat"


@dataclass
class RawLine:
    date: str
    time: str
    rest: str


def split_lines(text: str) -> list[RawLine]:
    """Group the export's lines into messages (continuation lines appended)."""
    out: list[RawLine] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        clean = line.translate(INVISIBLE).replace(" ", " ").replace(" ", " ")
        m = HEADER_IOS.match(clean) or HEADER_ANDROID.match(clean)
        if m:
            out.append(RawLine(m.group("date"), m.group("time").strip(), m.group("rest")))
        elif out:
            out[-1].rest += "\n" + line.translate(INVISIBLE)
    for raw in out:
        raw.rest = raw.rest.rstrip("\n")
    return out


def date_order(dates: Iterable[str], forced: Optional[str]) -> tuple[str, bool]:
    """Work out whether dates are day-month-year, month-day-year or year-first.

    Returns the order and whether it was a guess (all dates ambiguous).
    """
    if forced:
        return forced, False
    parts = [re.split(r"[./-]", d) for d in dates]
    if any(len(p[0]) == 4 for p in parts):
        return "ymd", False
    if any(int(p[0]) > 12 for p in parts):
        return "dmy", False
    if any(int(p[1]) > 12 for p in parts):
        return "mdy", False
    return "dmy", bool(parts)


def parse_stamp(raw_date: str, raw_time: str, order: str, zone: tzinfo) -> Optional[datetime]:
    a, b, c = (int(x) for x in re.split(r"[./-]", raw_date))
    if order == "ymd":
        year, month, day = a, b, c
    elif order == "mdy":
        month, day, year = a, b, c
    else:
        day, month, year = a, b, c
    if year < 100:
        year += 2000
    m = TIME_RE.match(raw_time.strip())
    if not m:
        return None
    hour, minute, second = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    ampm = (m.group(4) or "").lower()
    if ampm == "p" and hour < 12:
        hour += 12
    elif ampm == "a" and hour == 12:
        hour = 0
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=zone)
    except ValueError:
        return None


def split_sender(rest: str) -> tuple[Optional[str], str]:
    head, sep, body = rest.partition(": ")
    if sep and head and len(head) <= 80 and "\n" not in head:
        return head.strip(), body
    if rest.endswith(":") and "\n" not in rest:  # "Name:" with an empty body
        return rest[:-1].strip(), ""
    return None, rest


def person_for_name(name: str, me: Optional[str]) -> JsonDict:
    if me and name == me:
        return {"person_id": ME, "jid": None, "name": "Me", "push_name": None, "phone": None, "is_me": True}
    digits = re.sub(r"[\s()\-.]", "", name)
    if re.fullmatch(r"\+?\d{6,15}", digits):
        phone = digits if digits.startswith("+") else "+" + digits
        return {"person_id": "phone:" + phone, "jid": None, "name": name, "push_name": None, "phone": phone, "is_me": False}
    return {"person_id": "name:" + name, "jid": None, "name": name, "push_name": None, "phone": None, "is_me": False}


def classify(body: str, export: ExportFile) -> tuple[str, str, list[Attachment], bool]:
    """Return (type, text, attachments, deleted) for a message body."""
    stripped = body.translate(INVISIBLE).strip()
    low = stripped.lower()
    if low.rstrip(".") in DELETED_TEXTS:
        return "deleted", "", [], True

    m = ATTACHED_IOS.search(stripped)
    if m:
        name = m.group("file").strip()
        caption = (stripped[: m.start()] + stripped[m.end():]).strip()
        return attachment_message(name, caption, export)
    m = ATTACHED_ANDROID.match(stripped)
    if m:
        name = m.group("file")
        if safe_file_name(name) in export.members or m.group("note").strip().lower() in ANDROID_ATTACH_NOTES:
            return attachment_message(name, m.group("caption").strip(), export)

    bracket = re.fullmatch(r"<([^<>]{3,40})>", stripped)
    words = low.split()
    if bracket or (1 <= len(words) <= 3 and words[-1] in OMITTED_WORDS):
        phrase = (bracket.group(1) if bracket else stripped).lower()
        kind = next((v for k, v in OMITTED_KIND.items() if k in phrase), None)
        if kind or bracket:
            kind = kind or "media"
            return kind, "", [Attachment(None, None, None, None, missing=True)], False
    return "text", body.strip("\n"), [], False


def attachment_message(name: str, caption: str, export: ExportFile) -> tuple[str, str, list[Attachment], bool]:
    file_name = safe_file_name(name)
    src = export.members.get(name) or export.members.get(file_name)
    ctype = guess_type(file_name)
    size = None
    if isinstance(src, Path):
        size = src.stat().st_size
    elif isinstance(src, tuple):
        with zipfile.ZipFile(src[1]) as zf:
            size = zf.getinfo(src[2]).file_size
    kind = "document"
    if ctype:
        major = ctype.split("/")[0]
        kind = FILE_KINDS.get(major, "document")
    if "STICKER" in file_name.upper() or file_name.lower().endswith(".webp"):
        kind = "sticker"
    elif "GIF" in file_name.upper() and kind == "video":
        kind = "gif"
    elif file_name.lower().endswith(".vcf"):
        kind = "contact"
    return kind, caption, [Attachment(file_name, ctype, size, src, missing=src is None)], False


def read_phone_export(path: Path, name: Optional[str], me: Optional[str], zone: tzinfo,
                      order_forced: Optional[str], since: Optional[datetime], until: Optional[datetime],
                      data: Dataset, warn: Callable[[str], None]) -> None:
    export = load_export(path)
    lines = split_lines(export.text)
    if not lines:
        raise UserError(f"{path.name} doesn't look like a WhatsApp chat export (no message lines found).")
    order, guessed = date_order((l.date for l in lines), order_forced)
    if guessed:
        warn(f"{path.name}: every date could be read either way; assumed day.month. "
             "If times look wrong, run again with --date-order mdy.")

    chat_title = name or chat_name_from_label(export.label)
    chat_id = "txt:" + short_hash(chat_title, length=16)
    senders: dict[str, int] = {}
    parsed = []
    for raw in lines:
        stamp = parse_stamp(raw.date, raw.time, order, zone)
        sender, body = split_sender(raw.rest)
        # iPhone exports show group notices as if the group itself wrote them.
        if sender is not None and sender == chat_title:
            sender = None
        parsed.append((stamp, sender, body))
        if sender:
            senders[sender] = senders.get(sender, 0) + 1
    if me and senders and me not in senders:
        warn(f"{path.name}: nobody called '{me}' wrote in this chat; directions are left empty. "
             f"Senders found: {', '.join(senders)}")
    know_me = bool(me and me in senders)

    others = [s for s in senders if s != me]
    kind = "group" if len(others) > 1 or (not know_me and len(senders) > 2) else "direct"
    member_ids: list[str] = []
    if know_me:
        member_ids.append(data.add_person(person_for_name(me, me)))  # type: ignore[arg-type]
    for s in senders:
        if s != me:
            member_ids.append(data.add_person(person_for_name(s, me if know_me else None)))
    direct_pid = None
    if kind == "direct" and know_me and others:
        direct_pid = person_for_name(others[0], me)["person_id"]
    if chat_id in data.chats:
        raise UserError(f"Two inputs would both become the chat '{chat_title}'. Name them with --name, one file per run.")
    data.chats[chat_id] = {
        "chat_id": chat_id, "jid": None, "name": chat_title, "type": kind, "folder": None,
        "person_id": direct_pid, "member_ids": member_ids, "message_count": 0,
        "first_message_at": None, "last_message_at": None, "source": "phone-export",
    }
    data.inputs.append(path.name)

    occurrences: dict[str, int] = {}
    for stamp, sender, body in parsed:
        if not in_range(stamp, since, until):
            continue
        if sender:
            kind_, text, attachments, deleted = classify(body, export)
            pid = person_for_name(sender, me if know_me else None)["person_id"]
            direction = ("outgoing" if pid == ME else "incoming") if know_me else None
            sender_name = data.people[pid]["name"]
        else:
            kind_, text, attachments, deleted = "system", body.strip(), [], False
            pid, direction, sender_name = None, None, None
        base = short_hash(chat_id, iso(stamp), sender, body)
        n = occurrences.get(base, 0)
        occurrences[base] = n + 1
        record = message_record(
            message_id=f"{chat_id}:{base}" + (f"-{n}" if n else ""), chat_id=chat_id,
            sender_id=pid, sender_name=sender_name, direction=direction, type=kind_,
            sent_at=iso(stamp), sent_at_ms=to_ms(stamp), text=text, deleted=deleted,
            source="phone-export",
        )
        record["_attachments"] = attachments
        data.messages.append(record)

    if not know_me:
        warn(f"{path.name}: senders found: "
             + ", ".join(f"{s} ({n})" for s, n in sorted(senders.items(), key=lambda kv: -kv[1]))
             + ". Pass --me \"Your Name\" to mark which messages are yours.")


# =============================================================== merging ===


def load_previous(folder: Path) -> Dataset:
    """Read an earlier export made by this tool, so newer data can be merged in."""
    manifest = folder / "manifest.json"
    if not manifest.is_file():
        raise UserError(f"{folder} is not an export made by this tool (no manifest.json). "
                        "Exports made with --no-data can't be updated.")
    info = json.loads(manifest.read_text(encoding="utf-8"))
    if str(info.get("schema_version", "")).split(".")[0] != SCHEMA_VERSION.split(".")[0]:
        raise UserError(f"{folder} uses data format {info.get('schema_version')}, which this version can't merge.")

    def rows(name: str) -> list[JsonDict]:
        path = folder / name
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    data = Dataset(source=info.get("source", {}).get("kind", "desktop"))
    for p in rows("people.jsonl"):
        data.people[p["person_id"]] = p
    for c in rows("chats.jsonl"):
        data.chats[c["chat_id"]] = c
    for m in rows("messages.jsonl"):
        atts = []
        for a in m.get("attachments", []):
            src = (folder / a["path"]) if a.get("path") else None
            if src is not None and (not src.is_file() or folder.resolve() not in src.resolve().parents):
                src = None
            atts.append(Attachment(a.get("file_name"), a.get("content_type"), a.get("size_bytes"),
                                   src, missing=bool(a.get("missing")) or (a.get("path") is not None and src is None)))
        m["_attachments"] = atts
        data.messages.append(m)
    return data


def merge(old: Dataset, new: Dataset) -> tuple[Dataset, int]:
    """New data wins; messages that only the earlier export has are kept."""
    merged = Dataset(source=new.source, inputs=new.inputs)
    for pid, p in old.people.items():
        merged.people[pid] = p
    for pid, p in new.people.items():
        merged.people[pid] = p
    merged.chats.update(old.chats)
    for cid, c in new.chats.items():
        if cid in merged.chats:
            members = list(dict.fromkeys(c["member_ids"] + merged.chats[cid].get("member_ids", [])))
            c = dict(c, member_ids=members)
        merged.chats[cid] = c
    new_ids = {m["message_id"] for m in new.messages}
    kept = [m for m in old.messages if m["message_id"] not in new_ids]
    merged.messages = new.messages + kept
    return merged, len(kept)


# =============================================================== writing ===

PAGE_CSS = """
:root{--bg:#f4f1ec;--in:#fff;--out:#e9e2f6;--fg:#1d1a22;--muted:#6c6574;--sys:#f8ecd6;--link:#5e43a0}
@media (prefers-color-scheme:dark){:root{--bg:#17131c;--in:#241f2b;--out:#3a2f51;--fg:#ece8f1;--muted:#a29bab;--sys:#2a2431;--link:#c5adff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;background:var(--in);padding:12px 16px;border-bottom:1px solid rgba(0,0,0,.08);z-index:1}
header h1{margin:0;font-size:17px}header p{margin:2px 0 0;color:var(--muted);font-size:13px}
main{max-width:860px;margin:0 auto;padding:12px 16px 40px}
.day{text-align:center;margin:16px 0 8px}.day span{background:var(--in);color:var(--muted);font-size:12px;padding:4px 10px;border-radius:8px}
.msg{max-width:78%;margin:4px 0;padding:6px 9px 4px;border-radius:8px;background:var(--in);overflow-wrap:anywhere;clear:both}
.msg.out{margin-left:auto;background:var(--out)}.msg.sys{margin:8px auto;background:var(--sys);color:var(--muted);text-align:center;font-size:13px}
.who{font-weight:600;font-size:13px;color:var(--link)}.meta{color:var(--muted);font-size:11px;text-align:right;margin-top:2px}
.quote{border-left:3px solid var(--link);padding:2px 8px;margin:2px 0 4px;font-size:13px;color:var(--muted);background:rgba(0,0,0,.04);border-radius:4px}
.text{white-space:pre-wrap}a{color:var(--link)}img,video{max-width:100%;max-height:420px;border-radius:6px;display:block;margin:2px 0}
audio{width:100%;max-width:320px}.missing{color:var(--muted);font-style:italic}
"""

URL_RE = re.compile(r"https?://[^\s<>\"]+")


def linkify(text: str) -> str:
    out, last = [], 0
    for m in URL_RE.finditer(text):
        out.append(html.escape(text[last:m.start()]))
        url = m.group(0)
        out.append(f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{html.escape(url)}</a>')
        last = m.end()
    out.append(html.escape(text[last:]))
    return "".join(out)


def media_html(att: JsonDict, rel: str) -> str:
    if not att.get("path"):
        label = att.get("file_name") or "media"
        return f'<div class="missing">[{html.escape(label)} not included]</div>'
    href = html.escape(quote(rel), quote=True)
    ctype = att.get("content_type") or ""
    name = html.escape(att.get("file_name") or "file")
    if ctype.startswith("image/"):
        return f'<a href="{href}"><img src="{href}" alt="{name}" loading="lazy"></a>'
    if ctype.startswith("video/"):
        return f'<video controls preload="metadata" src="{href}"></video>'
    if ctype.startswith("audio/"):
        return f'<audio controls preload="none" src="{href}"></audio>'
    return f'<div>📎 <a href="{href}">{name}</a></div>'


def render_chat(chat: JsonDict, messages: list[JsonDict], zone: tzinfo, by_id: dict[str, JsonDict]) -> tuple[str, str]:
    folder = chat["folder"]
    title = html.escape(chat["name"] or "Chat")
    span = ""
    stamps = [m["sent_at"] for m in messages if m["sent_at"]]
    if stamps:
        first = datetime.fromisoformat(min(stamps).replace("Z", "+00:00")).astimezone(zone)
        last = datetime.fromisoformat(max(stamps).replace("Z", "+00:00")).astimezone(zone)
        span = f"{first:%Y-%m-%d} – {last:%Y-%m-%d} · "
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        f"<title>{title}</title><style>{PAGE_CSS}</style></head><body>",
        f"<header><h1>{title}</h1><p>{span}{len(messages)} messages · exported from WhatsApp</p></header><main>",
    ]
    md = [f"# {chat['name'] or 'Chat'}", "", f"{span}{len(messages)} messages, exported from WhatsApp", ""]
    current_day = None
    group = chat["type"] == "group"
    for m in messages:
        dt = datetime.fromisoformat(m["sent_at"].replace("Z", "+00:00")).astimezone(zone) if m["sent_at"] else None
        day = f"{dt:%A, %d %B %Y}" if dt else "Unknown date"
        if day != current_day:
            parts.append(f'<div class="day"><span>{html.escape(day)}</span></div>')
            md += ["", f"## {day}", ""]
            current_day = day
        clock = f"{dt:%H:%M}" if dt else ""
        if m["type"] == "system":
            if not m["text"]:
                continue  # group events without readable text stay in the data files only
            parts.append(f'<div class="msg sys">{linkify(m["text"])}{" · " + clock if clock else ""}</div>')
            md.append(f"*{clock} {m['text']}*".replace("\n", " "))
            continue
        cls = "msg out" if m["direction"] == "outgoing" else "msg"
        body = []
        if m["direction"] != "outgoing" and (group or m["direction"] is None) and m["sender_name"]:
            body.append(f'<div class="who">{html.escape(m["sender_name"])}</div>')
        reply = m.get("reply_to")
        if reply:
            quoted = reply.get("text") or ("(original not available)" if not reply.get("found") else "(media)")
            author = by_id.get(reply.get("message_id") or "", {}).get("sender_name") or ""
            body.append(f'<div class="quote">{html.escape(author + ": " if author else "")}{html.escape(quoted[:300])}</div>')
        for att in m["attachments"]:
            rel = os.path.relpath(att["path"], folder) if att.get("path") else ""
            body.append(media_html(att, rel))
        if m["deleted"]:
            body.append('<div class="text missing">This message was deleted</div>')
        elif m["location"]:
            lat, lon = m["location"]["latitude"], m["location"]["longitude"]
            url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}"
            body.append(f'<div>📍 <a href="{html.escape(url, quote=True)}">{lat:.5f}, {lon:.5f}</a></div>')
        if m["text"]:
            body.append(f'<div class="text">{linkify(m["text"])}</div>')
        body.append(f'<div class="meta">{"★ " if m.get("starred") else ""}{clock}</div>')
        parts.append(f'<div class="{cls}">{"".join(body)}</div>')

        who = m["sender_name"] or "Unknown"
        line = [f"**{who}** · {clock}"]
        if reply:
            line.append("> " + (reply.get("text") or "(original not available)").replace("\n", " ")[:200])
        for att in m["attachments"]:
            if att.get("path"):
                rel = quote(os.path.relpath(att["path"], folder))
                bang = "!" if (att.get("content_type") or "").startswith("image/") else ""
                line.append(f"{bang}[{att.get('file_name') or 'file'}]({rel})")
            else:
                line.append(f"*[{att.get('file_name') or 'media'} not included]*")
        if m["deleted"]:
            line.append("*This message was deleted*")
        if m["location"]:
            line.append(f"📍 {m['location']['latitude']}, {m['location']['longitude']}")
        if m["text"]:
            line.append(m["text"])
        md.append("  \n".join(line))
        md.append("")
    parts.append("</main></body></html>\n")
    return "".join(parts), re.sub(r"\n{3,}", "\n\n", "\n".join(md)).rstrip() + "\n"


def copy_attachment(att: Attachment, dest: Path) -> bool:
    src = att.source
    try:
        if isinstance(src, Path):
            shutil.copyfile(src, dest)
        elif isinstance(src, tuple) and src[0] == "zip":
            with zipfile.ZipFile(src[1]) as zf, zf.open(src[2]) as fin, open(dest, "wb") as fout:
                shutil.copyfileobj(fin, fout)
        elif isinstance(src, tuple) and src[0] == "text":
            dest.write_text(src[1], encoding="utf-8")
        else:
            return False
    except OSError:
        return False
    return True


def write_jsonl(path: Path, rows: list[JsonDict]) -> JsonDict:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"format": "jsonl", "records": len(rows), "sha256": digest}


def write_export(data: Dataset, out: Path, zone: tzinfo, zone_name: str, filters: JsonDict,
                 attachments: bool, with_data: bool, merged_from: bool) -> JsonDict:
    out.mkdir(parents=True)
    data.messages.sort(key=lambda m: (m["chat_id"], m["sent_at_ms"] if m["sent_at_ms"] is not None else -1, m["message_id"]))
    per_chat: dict[str, list[JsonDict]] = {}
    for m in data.messages:
        per_chat.setdefault(m["chat_id"], []).append(m)

    used_folders: set[str] = set()
    copied = missing = 0
    for cid in sorted(data.chats, key=lambda c: (data.chats[c]["name"] or "").casefold()):
        chat = data.chats[cid]
        msgs = per_chat.get(cid, [])
        if not msgs:
            continue
        base = safe_folder(chat["name"] or "chat")
        folder, n = base, 2
        while folder.casefold() in used_folders:
            folder, n = f"{base}-{n}", n + 1
        used_folders.add(folder.casefold())
        chat["folder"] = folder
        (out / folder).mkdir()
        used_files: set[str] = set()
        for m in msgs:
            records = []
            for att in m.pop("_attachments", []):
                if attachments and att.source is not None:
                    media = out / folder / "media"
                    media.mkdir(exist_ok=True)
                    name = att.file_name or "file"
                    stem, ext = os.path.splitext(name)
                    candidate, k = name, 2
                    while candidate.casefold() in used_files:
                        candidate, k = f"{stem}-{k}{ext}", k + 1
                    if copy_attachment(att, media / candidate):
                        used_files.add(candidate.casefold())
                        att.path = f"{folder}/media/{candidate}"
                        if att.size_bytes is None:
                            att.size_bytes = (media / candidate).stat().st_size
                        copied += 1
                    else:
                        att.missing = True
                else:
                    att.path = None
                    if att.source is None:
                        missing += 1
                records.append(att.record())
            m["attachments"] = records
        chat["message_count"] = len(msgs)
        stamps = [m["sent_at"] for m in msgs if m["sent_at"]]
        chat["first_message_at"] = min(stamps) if stamps else None
        chat["last_message_at"] = max(stamps) if stamps else None

    by_id = {m["message_id"]: m for m in data.messages}
    for cid, chat in data.chats.items():
        if chat.get("folder") and per_chat.get(cid):
            page, md = render_chat(chat, per_chat[cid], zone, by_id)
            (out / chat["folder"] / "index.html").write_text(page, encoding="utf-8")
            (out / chat["folder"] / "chat.md").write_text(md, encoding="utf-8")

    chats = [c for c in data.chats.values() if c.get("folder")]
    used_people = {pid for c in chats for pid in c["member_ids"]}
    used_people |= {m["sender_id"] for m in data.messages if m["sender_id"]}
    people = [p for pid, p in data.people.items() if pid in used_people]
    people.sort(key=lambda p: (not p["is_me"], (p["name"] or "").casefold()))
    chats.sort(key=lambda c: (c["name"] or "").casefold())

    stamps = [m["sent_at"] for m in data.messages if m["sent_at"]]
    manifest: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": {
            "kind": data.source,
            "app": "WhatsApp for macOS" if data.source == "desktop" else "WhatsApp chat export",
            "inputs": data.inputs,
            "platform": sys.platform,
        },
        "owner_id": ME,
        "timezone": zone_name,
        "filters": filters,
        "merged_with_earlier_export": merged_from,
        "counts": {"people": len(people), "chats": len(chats), "messages": len(data.messages),
                   "attachments_copied": copied, "attachments_missing": missing},
        "period": {"first_message_at": min(stamps) if stamps else None,
                   "last_message_at": max(stamps) if stamps else None},
        "files": {},
    }
    if with_data:
        manifest["files"]["people.jsonl"] = write_jsonl(out / "people.jsonl", people)
        manifest["files"]["chats.jsonl"] = write_jsonl(out / "chats.jsonl", chats)
        manifest["files"]["messages.jsonl"] = write_jsonl(out / "messages.jsonl", data.messages)
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


# ================================================================== CLI ===


def warn(message: str) -> None:
    print(f"Note: {message}", file=sys.stderr)


def cmd_inspect(args: argparse.Namespace) -> None:
    print(inspect_database(Path(args.source) / "ChatStorage.sqlite"))


def cmd_list(args: argparse.Namespace) -> None:
    container = Path(args.source)
    with tempfile.TemporaryDirectory(prefix="wa-export-") as tmp:
        conn = open_snapshot(container / "ChatStorage.sqlite", Path(tmp))
        check_schema(conn)
        chats = desktop_chats(conn)
        conn.close()
    if not chats:
        print("No chats found.", file=sys.stderr)
    for c in chats:
        last = f"{c['last']:%Y-%m-%d}" if c["last"] else "-"
        extra = f"\t{c['jid']}" if args.ids else ""
        print(f"{c['name']}\t{c['type']}, {c['count']} messages, last {last}{extra}")


def finish(args: argparse.Namespace, new: Dataset, zone: tzinfo, zone_name: str, filters: JsonDict) -> None:
    out = Path(args.out)
    merged_from = False
    if args.merge_from:
        old = load_previous(Path(args.merge_from))
        new, kept = merge(old, new)
        merged_from = True
        print(f"Kept {kept} messages that only the earlier export had.", file=sys.stderr)
    if not new.messages:
        print("Nothing to export: no matching messages.", file=sys.stderr)
        return
    manifest = write_export(new, out, zone, zone_name, filters, not args.no_attachments, not args.no_data, merged_from)
    counts = manifest["counts"]
    print(f"Exported {counts['messages']} messages in {counts['chats']} chat(s); "
          f"attachments: {counts['attachments_copied']} copied, {counts['attachments_missing']} not available.",
          file=sys.stderr)
    print(str(out))


def cmd_export(args: argparse.Namespace) -> None:
    zone, zone_name = local_zone(args.tz)
    since, until = parse_bound(args.since, False, zone), parse_bound(args.until, True, zone)
    with tempfile.TemporaryDirectory(prefix="wa-export-") as tmp:
        data = read_desktop(Path(args.source), args.chats, since, until, Path(tmp))
        filters = {"chats": [c["name"] for c in data.chats.values()] if args.chats else None,
                   "since": args.since, "until": args.until}
        finish(args, data, zone, zone_name, filters)


def cmd_import(args: argparse.Namespace) -> None:
    zone, zone_name = local_zone(args.tz)
    since, until = parse_bound(args.since, False, zone), parse_bound(args.until, True, zone)
    if args.name and len(args.files) > 1:
        raise UserError("--name only works with a single file.")
    data = Dataset(source="phone-export")
    data.add_person({"person_id": ME, "jid": None, "name": "Me", "push_name": None, "phone": None, "is_me": True})
    for f in args.files:
        read_phone_export(Path(f), args.name, args.me, zone, args.date_order, since, until, data, warn)
    filters = {"chats": None, "since": args.since, "until": args.until}
    finish(args, data, zone, zone_name, filters)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="whatsapp_export.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    def common_out(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--out", required=True, help="new output folder (must not exist)")
        sp.add_argument("--since", help="only messages from this date/time on")
        sp.add_argument("--until", help="only messages up to this date (inclusive) or time")
        sp.add_argument("--tz", help="time zone for dates and display, e.g. Europe/Zurich")
        sp.add_argument("--no-attachments", action="store_true")
        sp.add_argument("--no-data", action="store_true")
        sp.add_argument("--merge-from", help="earlier export to merge in (kept unchanged)")

    sp = sub.add_parser("inspect", help="show the database's tables and columns (no content)")
    sp.add_argument("--source", default=str(CONTAINER))
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("list", help="list chats in WhatsApp for macOS")
    sp.add_argument("--source", default=str(CONTAINER))
    sp.add_argument("--ids", action="store_true", help="also show WhatsApp IDs (phone numbers)")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("export", help="export chats from WhatsApp for macOS")
    sp.add_argument("--source", default=str(CONTAINER))
    sp.add_argument("--chats", help="comma-separated chat names as shown by list")
    common_out(sp)
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("import", help="convert chats exported on a phone (.zip or .txt)")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--me", help="your name as it appears in the chat")
    sp.add_argument("--name", help="chat name (default: from the file name)")
    sp.add_argument("--date-order", choices=["dmy", "mdy", "ymd"], help="force the date order")
    common_out(sp)
    sp.set_defaults(func=cmd_import)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "out", None) and Path(args.out).exists():
            raise UserError(f"Output folder already exists: {args.out}")
        args.func(args)
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
