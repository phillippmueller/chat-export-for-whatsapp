# Chat Export for WhatsApp (unofficial)

Export your WhatsApp chats to files on your own computer, just by asking
Claude. You get readable HTML and Markdown for each chat, the attachments, and
machine-readable JSON Lines files with stable IDs and a manifest, ready for a
database, a spreadsheet or further analysis. Earlier exports can be updated
with newer messages.

It reads WhatsApp for macOS directly, for every chat at once, or converts
chats you exported on your phone with WhatsApp's own **Export chat**.

> This is an independent, unofficial tool. It is not made by, endorsed by or
> affiliated with WhatsApp or Meta.

## Examples

- *"Export my WhatsApp chat with Anna."*
- *"Back up the Family group from this year, without the photos."*
- *"Here's the chat I exported from my phone, turn it into something I can read and search."*
- *"Update last month's WhatsApp export, then load messages.jsonl into DuckDB and show me who wrote the most."*

Claude checks that everything is ready, finds the exact chat name, runs the
export and tells you where the files are. Clicking Allow when macOS asks for
access to WhatsApp's data, and exporting a chat on your phone, stay with you
on purpose.

## Install

In the Claude app, add it from the plugin directory, or under **Customize →
Plugins → Add → Add marketplace → Add from a repository** with
`phillippmueller/chat-export-for-whatsapp`. In Claude Code:

```
/plugin install chat-export-for-whatsapp --marketplace phillippmueller/chat-export-for-whatsapp
```

## Requirements

- WhatsApp for macOS linked to your phone, or chats exported from your phone
- [uv](https://docs.astral.sh/uv/) (`brew install uv` on a Mac) or Python 3.9+
- Claude with a shell on that computer: Claude Code there, or the Claude
  desktop app with that computer linked

When Claude works from the app, the plugin's files live with Claude rather
than on your computer. Claude then asks before copying the plugin's two
scripts to `~/.chat-export-for-whatsapp/` on your computer and runs them from
there.

## What this plugin runs and accesses

Everything happens on your computer.

- **Downloads:** nothing itself. With uv, a Python 3.12 runtime once. The
  scripts use only Python's standard library.
- **Reads:** on a Mac, WhatsApp's data folder
  (`~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared`): a
  private, temporary copy of its chat and contacts databases, and the media
  files of the chats you export. For phone exports, only the files you name.
- **Writes:** only the new export folder you choose, by default a new folder
  under `~/whatsapp-chats/`. It never changes WhatsApp's data.
- **Runs:** the two scripts in `skills/whatsapp-export/scripts/`, and `pgrep`
  to check whether WhatsApp is running.
- **Sends:** nothing. There is no telemetry, no analytics and no network
  connection apart from uv's one-time Python download.

Claude reads the script's output (chat names, counts, folder paths) to report
back. The skill tells Claude not to read or quote message content unless you
ask for it.

## Privacy

Exports contain your and other people's private messages and phone numbers,
unencrypted. Store them safely, and think before uploading them anywhere: the
people in your chats didn't agree to their messages being stored elsewhere.
Full statement:
[PRIVACY.md](https://github.com/phillippmueller/chat-export-for-whatsapp/blob/main/PRIVACY.md).

## Limits

The Mac part exports what WhatsApp for macOS holds and needs macOS; an
update to WhatsApp's internal database format can break it until the plugin
is updated. Phone exports work on macOS and Linux, one chat per file, without
replies or reactions. Windows is not supported.

## Support

- Documentation, data format and source:
  https://github.com/phillippmueller/chat-export-for-whatsapp
- Questions and bugs:
  [open an issue](https://github.com/phillippmueller/chat-export-for-whatsapp/issues)
- Email: [philip.mueller.bueckeburg@gmail.com](mailto:philip.mueller.bueckeburg@gmail.com)
- Security problems: report privately, see
  [SECURITY.md](https://github.com/phillippmueller/chat-export-for-whatsapp/blob/main/SECURITY.md)

## License

MIT © Philip Mueller. "WhatsApp" is a trademark of WhatsApp LLC and is used
only to describe what the tool works with.
