# Machine-readable data format (schema 1.0)

Every export (unless run with `--no-data`) contains four files next to the
chat folders:

| File | Contents |
| :- | :- |
| `manifest.json` | What the export contains: versions, source, filters, counts, time span, checksums |
| `people.jsonl` | One line per person |
| `chats.jsonl` | One line per chat |
| `messages.jsonl` | One line per message, grouped by chat, oldest first |

The `.jsonl` files are [JSON Lines](https://jsonlines.org/): one complete JSON
object per line, UTF-8. Each file maps to one database table, and the IDs
link them like foreign keys:

```
people.person_id  <-  chats.person_id, chats.member_ids[],
                      messages.sender_id, messages.reply_to.author_id
chats.chat_id     <-  messages.chat_id
messages.message_id <- messages.reply_to.message_id
```

All timestamps are ISO-8601 in UTC (`2026-01-01T00:01:00.000Z`). Messages
also carry `sent_at_ms`, the same moment as Unix milliseconds.

The layout matches Chat Export for Signal's, so tools built for one work on
the other with small changes (`wa_key` instead of `signal_key`).

## Where the data comes from

`manifest.source.kind`, and `source` on every chat and message, say which
source a record came from:

- `desktop`: WhatsApp for macOS's local database. IDs are WhatsApp's own and
  stable.
- `phone-export`: a chat exported on a phone. That text format has no IDs, no
  phone numbers and no time zone, so IDs are derived from the content (see
  below) and times are read in the time zone recorded in
  `manifest.timezone`.

## Which ID to use

| ID | desktop | phone-export |
| :- | :- | :- |
| `person_id` | The person's WhatsApp ID, `<number>@s.whatsapp.net`. Where WhatsApp shows only a hidden-number ID (`<id>@lid`, common in groups), it is mapped to the phone-number ID when WhatsApp knows the number, else the `@lid` ID is used. `me` for you | `name:<name as shown>`, `phone:+<number>` if only a number was shown; `me` for you (with `--me`) |
| `chat_id` | `wa:<chat's WhatsApp ID>`, the same on every device | `txt:<hash of the chat name>` |
| `message_id` | `<chat_id>:<sender>:<WhatsApp message ID>`, the same on every device | `<chat_id>:<hash of time, sender and text>`, the same for every import of the same chat |
| `wa_key` | WhatsApp's own message ID | null |

To load repeated exports without duplicates, upsert on `message_id`. A phone
export and a Mac export of the same chat get different IDs, because the phone
format doesn't contain WhatsApp's.

## manifest.json

```json
{
  "schema_version": "1.0",
  "generator": {"name": "chat-export-for-whatsapp", "version": "0.1.0", "url": "..."},
  "created_at": "2026-10-20T09:21:23Z",
  "source": {"kind": "desktop", "app": "WhatsApp for macOS",
             "inputs": ["ChatStorage.sqlite"], "platform": "darwin"},
  "owner_id": "me",
  "timezone": "Europe/Zurich",
  "filters": {"chats": ["Anna Muster"], "since": null, "until": null},
  "merged_with_earlier_export": false,
  "counts": {"people": 3, "chats": 1, "messages": 1520,
             "attachments_copied": 210, "attachments_missing": 4},
  "period": {"first_message_at": "...", "last_message_at": "..."},
  "files": {
    "messages.jsonl": {"format": "jsonl", "records": 1520, "sha256": "..."}
  }
}
```

Check `schema_version` before loading. A change in the major number (2.0)
means fields were renamed or removed; a minor change (1.1) only adds fields.
The `sha256` values let you verify that no file was changed or cut off.

## people.jsonl

| Field | Type | Meaning |
| :- | :- | :- |
| `person_id` | string | Stable ID (see above) |
| `jid` | string or null | Phone-number WhatsApp ID, desktop only |
| `lid` | string or null | Hidden-number ID (`…@lid`) WhatsApp also uses for this person, desktop only |
| `name` | string | Name from your direct chat or contacts, else the group member list, WhatsApp's display name or the person's profile name, else the phone number (or the hidden ID if WhatsApp doesn't know the number); "Me" for you |
| `push_name` | string or null | Name the person set in their WhatsApp profile, desktop only |
| `phone` | string or null | Phone number in E.164 format, if known |
| `is_me` | boolean | True for the account owner |

## chats.jsonl

| Field | Type | Meaning |
| :- | :- | :- |
| `chat_id` | string | Stable ID (see above) |
| `jid` | string or null | The chat's WhatsApp ID, desktop only |
| `name` | string or null | Contact or group name |
| `type` | `"direct"`, `"group"` or `"broadcast"` | For phone exports, worked out from the number of people writing |
| `folder` | string | The chat's folder in this export (HTML, Markdown, media) |
| `person_id` | string or null | The other person, for direct chats |
| `member_ids` | list of strings | People in the chat that the source knows about, you included |
| `message_count` | integer | Messages exported for this chat |
| `first_message_at`, `last_message_at` | string or null | Time span of the exported messages |
| `source` | string | `desktop` or `phone-export` |

## messages.jsonl

| Field | Type | Meaning |
| :- | :- | :- |
| `message_id` | string | Stable ID (see above) |
| `wa_key` | string or null | WhatsApp's message ID (desktop) |
| `chat_id` | string | Chat the message belongs to |
| `sender_id` | string or null | `person_id` of the sender; null for system notices |
| `sender_name` | string or null | Sender's name at export time; "Me" for you |
| `direction` | `"incoming"`, `"outgoing"` or null | Null for system notices, and for phone exports imported without `--me` |
| `type` | string | `text`, `image`, `video`, `audio`, `document`, `sticker`, `gif`, `contact`, `location`, `link`, `deleted`, `system`, `media` (media of unknown kind) or `other` |
| `wa_type` | integer or null | WhatsApp's raw message type code (desktop), useful when `type` is `other` |
| `sent_at` | string or null | When the message was sent (UTC) |
| `sent_at_ms` | integer or null | Same, as Unix milliseconds |
| `text` | string | Message text or media caption, empty if there is none |
| `reply_to` | object or null | The message this one replies to (below), desktop only |
| `reactions` | list | Always empty in this version |
| `attachments` | list | Files sent with the message (below) |
| `location` | object or null | `latitude`, `longitude` for shared locations (desktop) |
| `deleted` | boolean | Deleted for everyone |
| `starred` | boolean | Starred by you (desktop) |
| `source` | string | `desktop` or `phone-export` |

`reply_to`: `wa_key`, `message_id`, `author_id`, `sent_at` and `text` of the
quoted message, and `found` (false if the original isn't in the export; then
only `wa_key` is set).

`attachments[]`: `file_name`, `content_type`, `size_bytes`, `path` (the
file's location relative to the export folder, null if it wasn't copied) and
`missing` (true if the file wasn't available: not downloaded on the Mac, or
left out of the phone export).

## Examples

DuckDB, straight from the files:

```sql
SELECT p.name, count(*) AS messages
FROM 'messages.jsonl' m JOIN 'people.jsonl' p ON m.sender_id = p.person_id
GROUP BY p.name ORDER BY messages DESC;
```

pandas:

```python
import pandas as pd
messages = pd.read_json("messages.jsonl", lines=True)
```

PostgreSQL, keeping each record as JSONB and upserting repeated exports:

```sql
CREATE TABLE whatsapp_messages (message_id text PRIMARY KEY, data jsonb);
-- for each line of messages.jsonl:
INSERT INTO whatsapp_messages VALUES ($1, $2)
ON CONFLICT (message_id) DO UPDATE SET data = EXCLUDED.data;
```

## Notes

- With `update` (or `--from`), the data files hold the earlier export's
  records plus everything new. Records present in both come from the new
  read.
- WhatsApp sometimes stores the same message twice (for example after a history
  sync). The export keeps one copy.
- The Status feed and channels you follow are left out.
- Messages of kinds this version doesn't recognise yet are kept with type
  `other` and their code in `wa_type`, and shown as a short note in the HTML.
- Group notices without readable text (WhatsApp stores many as codes) are kept
  in `messages.jsonl` with type `system` and empty text, but not shown in the
  HTML.
- Like the rest of the export, these files contain private messages and
  phone numbers, unencrypted.
