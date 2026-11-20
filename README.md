# srtrack — Oracle SR tracker

A fast, dependency-free command-line tool for tracking **Oracle Support Service
Requests (SRs)** from My Oracle Support. It keeps everything in one local SQLite
file and answers the question that matters each morning: *which SRs need me today?*

- Tracks severity, MOS status, product, environment, owner, Oracle engineer, linked bugs, tags
- Knows **who has the ball** for each MOS status: Oracle (NEW, WIP, DEV, RVW) or us (CUS, SOL, ACL)
- Follow-up dates with shorthand: `today`, `tomorrow`, `+3d`, `+2w`, `fri`
- Full history: every field change and note is timestamped
- Refer to SRs by a **unique suffix** — `srt show 6789` instead of `3-38123456789`
- Daily agenda, full-text search (including notes), stats, CSV/JSON export, CSV import

Requires Python 3.9+ and nothing else.

## Install

```bash
pip install .
```

Or run without installing:

```bash
python -m srtrack --help
```

Data is stored in `~/.local/share/srtrack/srs.db`. Override it with `--db FILE` or the
`SRT_DB` environment variable (e.g. point it to a shared drive). Set `SRT_OWNER` to
fill in the owner on new SRs automatically.

## Daily workflow

```bash
# Log a new SR
srt add 3-38123456789 "ORA-00600 [kcbz_check_objd_typ] after 19.21 RU" \
    -s 1 -p "Oracle Database 19c" --env "PROD exa01" --tags prod,rac \
    -n "Upload TFA collection" -f today

# Oracle asked for something: status -> Customer Working, set next action & follow-up
srt st 6789 cus -n "Send AWR 10:00-12:00" -f tomorrow

# Record an update (use '-' to paste a long update from stdin)
srt note 6789 Uploaded AWR and TFA --status RVW -f +2d

# Link an Oracle bug
srt bug 6789 35123456

# What needs attention? (also what plain `srt` shows)
srt today

# Close it
srt close 6789 -r "Applied one-off patch 35123456"
```

## Commands

| Command | Purpose |
|---|---|
| `srt today` | Agenda: follow-ups due, SRs waiting on us, SRs with no activity for 7+ days, auto-close warnings |
| `srt add NUMBER TITLE [opts]` | Add an SR |
| `srt ls [filters]` | List open SRs, sorted by severity → waiting on us → follow-up |
| `srt show SR...` | Details and full history |
| `srt update SR [opts]` | Change any field (`-s`, `--status`, `-p`, `-o`, `-e`, `--env`, `-n`, `-f`, `--tags`, `--bugs`, `-t`, `-r`) |
| `srt st SR STATUS` | Quick status change |
| `srt note SR TEXT` | Add a note (optionally `--status`, `-f`, `-n` at the same time) |
| `srt close SR [-r TEXT]` / `srt reopen SR` | Close / reopen |
| `srt tag SR TAG...` / `srt bug SR BUG...` | Add tags / bugs (`--remove` to remove) |
| `srt search TEXT` | Search numbers, titles, actions, bugs, tags and notes |
| `srt stats` | Counts by severity/status/product, aging, time to close |
| `srt export [-o file.csv\|file.json]` | Export (JSON includes history) |
| `srt import FILE.csv [--update]` | Import from a spreadsheet; headers like `SR Number`, `Problem Summary`, `Sev` are recognised |
| `srt statuses` | Show status codes |
| `srt rm SR` | Delete an SR and its history |

List filters: `--status CUS` (repeatable), `-s 1` (repeatable), `-w us|oracle`, `-p PRODUCT`,
`-o OWNER`, `-t TAG`, `-a` (include closed), `-c` (only closed), `-d` (follow-ups due),
`--sort priority|follow_up|opened|updated|severity|number`, `-l N`.

## Status codes

| Code | MOS status | Waiting on |
|---|---|---|
| NEW | New | Oracle |
| WIP | Work In Progress | Oracle |
| DEV | Development Working | Oracle |
| RVW | Review Update | Oracle |
| CUS | Customer Working | us |
| SOL | Solution Offered | us |
| ACL | Auto-Close Pending | us |
| CLO | Closed | — |

Long names work too: `--status "work in progress"`, `srt st 6789 closed`.

## Tests

```bash
python -m unittest
```
