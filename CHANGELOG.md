# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed (from the first test on real data)

- People who appear in groups only under a hidden-number ID (`…@lid`) are now
  mapped to their phone number via WhatsApp's `LID.sqlite`, so one person gets
  one record and a real name instead of a number. Real names now win over
  chat names that are just phone numbers.
- Messages WhatsApp stores twice are exported once (they caused duplicate
  `message_id`s).
- Photos, voice notes and other media that were never downloaded to the Mac
  now appear as missing attachments instead of empty messages.
- The Status feed and followed channels are left out.
- Messages of unknown kinds show a short note in the HTML instead of an empty
  bubble.
- `--chats` matches names with commas and with WhatsApp's invisible direction
  marks (as around phone numbers), suggests close names when nothing matches,
  and says when a name fits several chats.

## [0.1.0] - 2026-10-20

### Added

- `whatsapp-export.sh` with `doctor`, `list`, `export`, `update`, `import`,
  `inspect`, `version` and `help` commands.
- WhatsApp for macOS: reads a read-only, consistent snapshot of the app's
  local database (SQLite backup API), with names from the app's contacts,
  group members (including hidden-number IDs), replies, locations, stars,
  deleted messages and media files. Checks the database's tables and columns
  first and explains what's missing if WhatsApp changed its format.
- Phone exports: converts WhatsApp's own "Export chat" files (.zip, .txt or
  an unpacked folder) from iPhone and Android, with multi-line messages,
  attachments, omitted media, deleted messages, 12- and 24-hour times and
  automatic day/month order detection. `--me` marks your own messages.
- Same output for both: HTML and Markdown per chat, media, and
  `people.jsonl`, `chats.jsonl`, `messages.jsonl` and `manifest.json` (schema
  1.0, same layout as Chat Export for Signal). Documented in
  `reference/data-format.md`.
- Updates (`update --from`, `import --from`) merge an earlier export: new data
  wins, and messages only the earlier export has are kept.
- Safety checks: never writes to WhatsApp's data, refuses to overwrite an
  existing folder or to export into the repository, keeps attachment names
  from escaping the export folder, escapes all message text in HTML.
- `whatsapp-export` Claude skill, plugin and marketplace manifests, icon.
- Tests with fake data only: script tests, converter unit tests and an
  end-to-end run; CI on macOS and Linux; release workflow publishing the
  skill as a ZIP.
