# Chat Export for WhatsApp (unofficial)

Export your WhatsApp chats to files on your own computer, either by asking
Claude or yourself from the Terminal.

- **HTML** you open in a browser, formatted like the chat, with images inline
- **Markdown** for notes and archives
- **Machine-readable data**: `messages.jsonl`, `chats.jsonl` and
  `people.jsonl` with stable IDs, plus a `manifest.json` with counts and
  checksums, ready for a database, pandas or an AI pipeline
- **Attachments** (images, voice notes, files) copied alongside
- **Updates**: merge newer messages into an earlier export without touching it

Two sources, same output:

- **WhatsApp for macOS**: reads the app's local database directly, read-only,
  for every chat at once. WhatsApp can stay open.
- **Phone exports**: converts chats you exported with WhatsApp's own
  **Export chat** on iPhone or Android (`.zip` or `.txt`), on macOS or Linux.

Everything runs locally. Nothing is uploaded anywhere.

> This is an independent, unofficial tool. It is not made by, endorsed by or
> affiliated with WhatsApp or Meta.

## Examples

Once installed as a Claude plugin or skill, ask Claude:

- *"Export my WhatsApp chat with Anna."*
- *"Back up the Family group from this year, without the photos."*
- *"Here's the chat I exported from my phone, turn it into something I can read and search."*
- *"Update last month's WhatsApp export, then load messages.jsonl into DuckDB and show me who wrote the most."*

Claude checks that everything is ready, finds the exact chat name, runs the
export and tells you where the files are. Clicking Allow when macOS asks for
access to WhatsApp's data, and exporting a chat on your phone, stay with you
on purpose.

## Install

For the Mac part you need [WhatsApp for macOS](https://www.whatsapp.com/download),
linked to your phone. For both parts you need [uv](https://docs.astral.sh/uv/)
(`brew install uv` on a Mac) or Python 3.9 or newer. Claude needs a shell on
the computer with the data: the Claude desktop app with that computer linked,
or Claude Code on it.

**Claude app** (paid plans): open **Customize → Plugins**, click **Add → Add
marketplace → Add from a repository**, enter
`phillippmueller/chat-export-for-whatsapp`, and install **Chat Export for
WhatsApp**.

**Single skill upload** (any plan with code execution on): download
`chat-export-for-whatsapp-skill.zip` from the
[latest release](https://github.com/phillippmueller/chat-export-for-whatsapp/releases/latest),
then open **Customize → Skills → + → Create skill → Upload a skill**.

**Claude Code:**

```bash
claude plugin marketplace add phillippmueller/chat-export-for-whatsapp
claude plugin install chat-export-for-whatsapp@chat-export-for-whatsapp
```

When Claude works from the app, the plugin's files live with Claude rather
than on your computer. Claude then asks before copying its two scripts to
`~/.chat-export-for-whatsapp/` on your computer and runs them from there.

## Use it without Claude

```bash
git clone https://github.com/phillippmueller/chat-export-for-whatsapp.git
cd chat-export-for-whatsapp
S=plugins/chat-export-for-whatsapp/skills/whatsapp-export/scripts/whatsapp-export.sh
```

From WhatsApp for macOS:

```bash
$S doctor                              # checks everything, changes nothing
$S list                                # exact chat names
$S export --chats "Anna Muster"
```

The first time, macOS may ask whether Terminal may access data from other
apps. Click **Allow**.

From a phone export (on the phone: open the chat → Export chat → Attach
media, and send the file to your computer):

```bash
$S import ~/Downloads/"WhatsApp Chat - Family.zip" --me "Your Name"
```

Your export lands in a new dated folder under `~/whatsapp-chats/`:

```
export-2026-10-20_141500/
├── manifest.json        what's inside: versions, source, counts, checksums
├── messages.jsonl       one message per line, with stable IDs
├── chats.jsonl          one chat per line
├── people.jsonl         one person per line
└── AnnaMuster/
    ├── index.html       open this in your browser
    ├── chat.md
    └── media/           attachments
```

### Commands

| Command | What it does |
| :- | :- |
| `doctor` | Checks Python/uv, WhatsApp's data folder and permissions |
| `list` | Lists the chats in WhatsApp for macOS |
| `export` | Exports chats from WhatsApp for macOS to a new folder |
| `update --from DIR` | Exports again and merges in an earlier export (kept unchanged) |
| `import FILE...` | Converts chats exported on a phone |
| `inspect` | Shows the database's tables and columns, no messages (for bug reports) |
| `help` | Shows all options |

Useful options:

```bash
--chats "Anna,Family"         # export/update: only these chats (default: all)
--me "Your Name"              # import: mark your own messages
--since 2026-01-01            # only messages from this date
--until 2026-03-31            # only messages up to and including this date
--tz Europe/Zurich            # time zone (default: this computer's)
--out ~/whatsapp-chats/anna   # your own output folder (must not exist yet)
--no-attachments              # skip images and files
--no-data                     # skip the .jsonl files and manifest.json
```

## Try it with sample data

`tests/demo.sh` builds fake WhatsApp data with invented people and messages,
both a fake Mac database and fake phone exports, and exports them with the
real script. It never reads your own WhatsApp data:

```bash
tests/demo.sh                 # writes to ~/whatsapp-export-demo
open ~/whatsapp-export-demo/desktop-export/AnnaMuster/index.html
```

## Machine-readable data

The `.jsonl` files hold one JSON object per line and map one-to-one onto
database tables, linked by IDs:

```json
{"message_id": "wa:4915…@s.whatsapp.net:4915…@s.whatsapp.net:3EB0…", "wa_key": "3EB0…",
 "chat_id": "wa:4915…@s.whatsapp.net", "sender_id": "4915…@s.whatsapp.net",
 "sender_name": "Anna Muster", "direction": "incoming", "type": "text",
 "sent_at": "2026-01-01T00:01:00.000Z", "text": "Hi! Are we still on for Friday?",
 "reply_to": null, "attachments": [], "source": "desktop", …}
```

The layout matches
[Chat Export for Signal](https://github.com/phillippmueller/chat-export-for-signal)'s,
so the same queries work on both. For example, with DuckDB:

```sql
SELECT sender_name, count(*) FROM 'messages.jsonl' GROUP BY 1 ORDER BY 2 DESC;
```

Full field reference, ID rules and loading examples:
[data format](plugins/chat-export-for-whatsapp/skills/whatsapp-export/reference/data-format.md).

## What this plugin runs and accesses

Everything happens on your computer. In detail:

- **Downloads:** nothing itself. If you use uv, uv downloads a Python 3.12
  runtime once from its official source. The scripts use only Python's
  standard library, so no packages are installed.
- **Reads:** on a Mac, WhatsApp's data folder
  (`~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared`): the chat
  database `ChatStorage.sqlite`, `ContactsV2.sqlite` and `LID.sqlite` for
  names and phone numbers, and the media files of the chats you export. The databases are
  copied to a private temporary folder first and only that copy is read; the
  copy is deleted afterwards. For imports, only the files you name.
- **Writes:** only the new export folder you choose (by default a new folder
  under `~/whatsapp-chats/`). It never changes WhatsApp's data, and it
  refuses to write into this repository.
- **Runs:** the two scripts in
  [`plugins/chat-export-for-whatsapp/skills/whatsapp-export/scripts/`](plugins/chat-export-for-whatsapp/skills/whatsapp-export/scripts/), and
  `pgrep` to check whether WhatsApp is running.
- **Sends:** nothing. There is no telemetry, no analytics and no network
  connection apart from uv's one-time Python download.

When you use it through Claude, Claude reads the script's output (chat names,
counts, folder paths) to report back. The skill tells Claude not to read or
quote message content unless you ask for it.

## Privacy

Exports contain **your and other people's private messages and phone numbers,
unencrypted**.

- Store them somewhere safe, and delete them when you no longer need them.
- The people in a chat didn't agree to their messages being stored elsewhere.
  Think before uploading exports to cloud drives, databases or AI tools.
- The script refuses to write exports inside this repository, and
  `.gitignore` blocks common export folders, so they can't be committed by
  accident.

See [PRIVACY.md](PRIVACY.md) for the privacy statement.

## Limits

- **Mac app:** only what WhatsApp for macOS holds: what synced when you
  linked it, plus everything since. Media you never opened on the Mac may be
  missing. Reactions and polls aren't exported yet. WhatsApp's database
  format isn't public, so an app update can break the Mac part until this
  tool is updated (phone exports keep working).
- **Phone exports:** one chat per file, no replies or reactions, no IDs that
  match the Mac app's, and WhatsApp caps how much an export contains. Formats
  differ by phone language: English and German are tested, others are read
  on a best-effort basis.
- Windows is not supported.

## Support

- **Questions and bugs:** [open an issue](https://github.com/phillippmueller/chat-export-for-whatsapp/issues)
- **Email:** [philip.mueller.bueckeburg@gmail.com](mailto:philip.mueller.bueckeburg@gmail.com)
- **Security problems:** please report them privately, see [SECURITY.md](SECURITY.md)

## How it works

[`whatsapp-export.sh`](plugins/chat-export-for-whatsapp/skills/whatsapp-export/scripts/whatsapp-export.sh)
is a small, defensive wrapper: it checks the setup, keeps exports out of the
repository and starts
[`whatsapp_export.py`](plugins/chat-export-for-whatsapp/skills/whatsapp-export/scripts/whatsapp_export.py)
through uv or python3. The Python script takes a consistent snapshot of
WhatsApp's database with SQLite's backup API on a read-only connection,
checks that the tables and columns it needs are there, and writes HTML,
Markdown and the data files. For phone exports it parses the text format of
iPhone and Android exports, including multi-line messages, attachments and
both date orders.

```
.claude-plugin/marketplace.json      marketplace manifest (lists the plugin)
plugins/chat-export-for-whatsapp/    the plugin, exactly what users install
  .claude-plugin/plugin.json         plugin manifest and icon
  README.md                          listing text for the plugin directory
  skills/whatsapp-export/
    SKILL.md                         instructions Claude follows
    scripts/                         the two scripts
    reference/                       data format and troubleshooting
tests/                               tests and the sample-data demo (no real WhatsApp data)
.github/workflows/                   CI and release
```

## Development

```bash
tests/test_cli.sh                                    # script tests
python3 -m unittest discover -s tests -p "test_*.py" # converter tests
tests/test_e2e.sh                                    # full run on fake data
shellcheck plugins/chat-export-for-whatsapp/skills/whatsapp-export/scripts/*.sh tests/*.sh
claude plugin validate --strict .                    # check the marketplace manifest
claude plugin validate --strict plugins/chat-export-for-whatsapp  # check the plugin
```

Every push runs these checks on macOS and Linux via GitHub Actions. None of
them need network access or real data.

## License

[MIT](LICENSE) © Philip Mueller. "WhatsApp" is a trademark of WhatsApp LLC
and is used here only to describe what the tool works with.
