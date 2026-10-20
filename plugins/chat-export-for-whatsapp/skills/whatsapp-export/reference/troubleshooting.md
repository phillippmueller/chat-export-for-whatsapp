# Troubleshooting

## "macOS blocked access to WhatsApp's data"

WhatsApp keeps its chats in its own protected folder. The first time, macOS
asks whether the app running the script (Terminal, or the app Claude uses)
may access data from other apps. Run the command again and click **Allow**.

If you clicked Don't Allow before, macOS won't ask again. Open **System
Settings > Privacy & Security > Full Disk Access** (or **App Management** /
**Files and Folders**, depending on your macOS version), and turn it on for
that app. Then quit and reopen the app and try again.

## "No WhatsApp for macOS data found"

The Mac part reads the data of WhatsApp for macOS (from the Mac App Store or
whatsapp.com), in:

```
~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite
```

Install the app, link it to your phone, and let it finish syncing. WhatsApp
Web in a browser keeps nothing this tool can read; use a phone export
instead. If the data is somewhere else (a copy, another user account), pass
the folder with `--source DIR`.

## "Neither uv nor Python 3.9+ was found"

Install uv once, following its official guide:
https://docs.astral.sh/uv/getting-started/installation/ (on a Mac, Homebrew
users can install the `uv` package). The first run then takes a little longer
while uv downloads Python; later runs reuse it.

## "WhatsApp's database format is not what this tool expects"

WhatsApp's database isn't a public format, and an app update can change it.
The message names the table or column that's missing. Please:

1. Check for a new release of this plugin.
2. Run `whatsapp-export.sh inspect`. It prints only table names, column
   names and row counts, no messages, names or numbers.
3. Open an issue with that output and your WhatsApp version (WhatsApp >
   About WhatsApp).

Phone exports (`import`) keep working in the meantime.

## A chat is missing from `list`

Chats without messages and the Status feed are left out. Check that the chat
shows its messages in WhatsApp on the Mac; if the history is only on your
phone, export it there and use `import`.

## Many attachments are "not available"

On the Mac, WhatsApp only keeps the photos, videos and files you opened or
that downloaded automatically. Open the chat in WhatsApp on the Mac and
scroll through it so the media downloads, then export again. For phone
exports, choose **Attach media** when exporting; WhatsApp still leaves out
some media in very large exports.

## Times in a phone export are wrong

- **Day and month swapped** (e.g. 3 February instead of 2 March): the dates
  in the file could be read either way. Run again with `--date-order mdy` or
  `--date-order dmy`.
- **Off by some hours:** phone exports contain no time zone. Pass the zone
  the phone was set to, e.g. `--tz Europe/Zurich`.

## My messages aren't marked as mine (phone export)

Pass your name exactly as it appears in the chat with `--me "Your Name"`.
Without `--me`, the import prints every name it found with a count.

## A phone export isn't recognised

The script expects WhatsApp's own export: a `.zip` with one `.txt` inside
(often `_chat.txt`), or that `.txt` itself, with one line per message
starting with a date and time. If your phone's language uses a format the
script doesn't read, please open an issue with two or three made-up example
lines in the same format (never real messages).

## "Output folder already exists"

Each export goes into a new folder so nothing gets overwritten. Pick another
name with `--out`, or delete the old folder first.

## "Refusing to export into this tool's repository"

A safety check: exports are private messages and must never be committed to
git. Choose an output folder outside the repository.

## "is not an export made by this tool"

`update` and `--from` need an earlier export from this tool, with its
`manifest.json`. Exports made with `--no-data` can't be updated.
